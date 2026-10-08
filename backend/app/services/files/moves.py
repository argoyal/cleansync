"""Carry out planned renames and moves. Inside a sync pair, an item that is already on
Google Drive is moved there too (a server-side move, so file IDs and sharing links stay),
then locally; the next sync then sees the same change on both sides and has nothing to do.

Order: check everything, keep the pair's syncs off, move on Drive, then on disk. If the disk
move fails, the Drive move is undone (best effort). If the Drive move fails, nothing changes.
"""

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from app.core.pathsafe import safe_join
from app.models import Account, SyncPair
from app.models.enums import AccountStatus
from app.paths import Layout
from app.services.files import ops
from app.services.files.ops import FileOpError, MoveStep, PartialError, UndoFailed
from app.services.files.scope import Scope, join_rel, pair_for
from app.services.rclone.commands import RcloneEnv, lsjson_args, moveto_args, remote_target
from app.services.rclone.errors import Failure, classify_failure
from app.services.rclone.runner import RunResult, run_rclone

log = logging.getLogger(__name__)

BUSY_MSG = "this sync pair is busy; try again when the current job finishes"
DISABLED_MSG = "database integrity check failed; restore a backup (see docs/backup-recovery.md)"
AUTH_REASON = "Google says the login expired or was revoked"
DRIVE_TIMEOUT_S = 120.0


def _drive_error(res: RunResult, what: str) -> str:
    detail = "; ".join(res.error_tail[-2:]) or f"rclone exited with {res.exit_code}"
    return f"Google Drive refused to {what}: {detail}"


class DriveMover:
    """Server-side moves and existence checks on an account's Google Drive."""

    def __init__(
        self,
        *,
        layout: Layout,
        env: RcloneEnv,
        accounts,
        runner=run_rclone,
        timeout_s: float = DRIVE_TIMEOUT_S,
    ) -> None:
        self.layout = layout
        self.env = env
        self.accounts = accounts
        self.runner = runner
        self.timeout_s = timeout_s

    async def _rclone(self, args: list[str], out: list[str] | None = None) -> RunResult:
        call = self.runner(
            args,
            self.env.env(),
            log_path=self.layout.logs_dir / "rclone-fileops.log",
            on_stdout=out.append if out is not None else None,
            secrets=[self.env.config_pass],
        )
        try:
            # On timeout the runner is cancelled, and its cleanup stops rclone.
            return await asyncio.wait_for(call, self.timeout_s)
        except TimeoutError:
            log.warning("rclone did not finish a file action within %ss", self.timeout_s)
            raise FileOpError(
                "Google Drive did not answer in time. Check the item on Google Drive; the "
                "next sync will reconcile any change.",
                504,
            ) from None

    async def _failed(self, account: Account, res: RunResult, what: str) -> FileOpError:
        if classify_failure(res.exit_code, res.error_tail) == Failure.AUTH:
            await self.accounts.mark_needs_reauth(account.id, AUTH_REASON)
        message = _drive_error(res, what)
        log.warning("file action on Google Drive failed: %s", message)
        return FileOpError(message, 502)

    async def exists(self, account: Account, target: str) -> bool:
        """Whether Drive has an item at target. Fails closed: unsure answers raise."""
        out: list[str] = []
        res = await self._rclone(lsjson_args(self.env, target, stat=True), out)
        if res.exit_code != 0:
            # rclone's documented exit codes: 3 = directory not found, 4 = file not found.
            if res.exit_code in (3, 4):
                return False
            raise await self._failed(account, res, "check the folder")
        try:
            item = json.loads("\n".join(out))
        except ValueError:
            item = None
        if not isinstance(item, dict):
            raise FileOpError("Google Drive gave an answer DriveSync could not read", 502)
        return True

    async def move(self, account: Account, src: str, dst: str) -> None:
        res = await self._rclone(moveto_args(self.env, src, dst))
        if res.exit_code != 0:
            raise await self._failed(account, res, "move the item")


@dataclass
class _Planned:
    step: MoveStep
    src_target: str | None = None  # set when the item is moved on Drive too
    dst_target: str | None = None


@dataclass
class MovePlan:
    scope: Scope
    items: list[_Planned]

    @property
    def drive_pairs(self) -> set[int]:
        return {p.step.pair.id for p in self.items if p.src_target and p.step.pair}

    @property
    def touched(self) -> list[str]:
        return [r for p in self.items for r in (p.step.src, p.step.dst)]


def touched_pairs(scope: Scope, rels: Iterable[str]) -> list[int]:
    ids: list[int] = []
    for rel in rels:
        pair = pair_for(scope.pairs, rel)
        if pair is not None and pair.id not in ids:
            ids.append(pair.id)
    return ids


def check_pairs(queue, pair_ids: Iterable[int]) -> None:
    """Refuse when syncing is off or a pair is busy; before anything changes."""
    if getattr(queue, "disabled_reason", None) is not None:
        raise FileOpError(DISABLED_MSG, 503)
    for pid in pair_ids:
        if queue.is_busy(pid):
            raise FileOpError(BUSY_MSG)


@contextlib.contextmanager
def hold_pairs(queue, pair_ids: Iterable[int]) -> Iterator[None]:
    """Keep the pairs' syncs from starting while their files change."""
    with contextlib.ExitStack() as held:
        for pid in pair_ids:
            try:
                held.enter_context(queue.hold(pid))
            except RuntimeError:
                raise FileOpError(BUSY_MSG) from None
        yield


def _target(account: Account, pair: SyncPair, prel: str) -> str:
    return remote_target(account.remote_name, join_rel(pair.remote_path, prel))


async def prepare_moves(
    scope: Scope, steps: list[MoveStep], *, queue, catalog, drive: DriveMover
) -> MovePlan:
    """Decide which items move on Drive too, and check everything before anything moves."""
    plan = MovePlan(scope, [_Planned(s) for s in steps])
    check_pairs(queue, touched_pairs(scope, plan.touched))
    account = scope.account
    connected = account.status == AccountStatus.CONNECTED
    listings: dict[int, object] = {}
    for item in plan.items:
        pair = item.step.pair
        if pair is None:
            continue
        if pair.id not in listings:
            listings[pair.id] = await asyncio.to_thread(catalog.load, pair.id)
        listing = listings[pair.id]
        src_prel, dst_prel = item.step.src_prel, item.step.dst_prel
        src_target = _target(account, pair, src_prel)
        # The listing can only confirm an item is on Drive: it may be up to an hour old.
        on_drive = listing is not None and src_prel in listing.files
        if not on_drive and (pair.initialized or listing is None):
            if connected:
                on_drive = await drive.exists(account, src_target)
            elif pair.initialized:  # no listing, or one that may be out of date
                raise FileOpError(
                    "DriveSync can't tell whether this item is on Google Drive until the Google "
                    "login is reconnected."
                )
        if not on_drive:
            continue  # not on Drive yet: the next sync uploads it under the new name
        if not connected:
            raise FileOpError(
                "This item is on Google Drive, and the Google login needs to be reconnected "
                "before it can be moved."
            )
        dst_target = _target(account, pair, dst_prel)
        case_only = dst_prel.casefold() == src_prel.casefold()
        if not case_only and (
            (listing is not None and dst_prel in listing.files)
            or await drive.exists(account, dst_target)
        ):
            raise FileOpError(f"'{dst_prel}' is already on Google Drive")
        item.src_target, item.dst_target = src_target, dst_target
    return plan


async def _undo_drive_move(account: Account, item: _Planned, drive: DriveMover) -> bool:
    try:
        await drive.move(account, item.dst_target, item.src_target)  # type: ignore[arg-type]
        return True
    except Exception:  # a cancellation is not swallowed
        log.exception("could not undo the Google Drive move of %s", item.step.src)
        return False


async def finish_in_thread(fn, *args):
    """Run fn in a worker thread and always wait for it to end, even when the request is
    cancelled: a thread can't be stopped, so the caller must not go on (or release a pair)
    while it may still be changing files. Returns (result, error, cancelled)."""
    task = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    cancelled = False
    while True:
        try:
            await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done():
                break
            cancelled = True  # wait on; the caller re-raises once the thread is done
        except Exception:
            break
    error = task.exception()
    return (None if error else task.result()), error, cancelled


def _moved_on_disk(scope: Scope, step: MoveStep) -> bool | None:
    """True: the item is at its new place; False: still at the old one; None: unclear."""
    src = safe_join(scope.root, step.src)
    dst = safe_join(scope.root, step.dst)
    src_there = src.exists() or src.is_symlink()
    dst_there = dst.exists() or dst.is_symlink()
    if src_there and dst_there and os.path.samefile(src, dst):
        # A case-only rename on a case-insensitive disk: look at the name the folder lists.
        names = set(os.listdir(dst.parent))
        if dst.name in names and src.name not in names:
            return True
        if src.name in names and dst.name not in names:
            return False
        return None
    if dst_there and not src_there:
        return True
    if src_there and not dst_there:
        return False
    return None


async def _move_one(plan: MovePlan, item: _Planned, drive: DriveMover) -> None:
    account = plan.scope.account
    on_drive = bool(item.src_target and item.dst_target)
    if on_drive:
        await drive.move(account, item.src_target, item.dst_target)  # type: ignore[arg-type]
    _, error, cancelled = await finish_in_thread(ops.apply_local, plan.scope, item.step)
    if error is None and not cancelled:
        return
    if on_drive:
        # Decide from the disk, not from the error: a cancelled request may still have
        # finished the local move in its thread.
        moved, check_error, again = await finish_in_thread(_moved_on_disk, plan.scope, item.step)
        cancelled = cancelled or again
        if error is None and moved is not True:
            error = FileOpError("the local move did not finish", 500)
        if moved is False:
            if not await _undo_drive_move(account, item, drive) and error is not None:
                raise UndoFailed(error) from error
        elif moved is None or check_error is not None:
            log.warning("disk state of %s unclear after a failed move", item.step.src)
            if error is not None:
                raise UndoFailed(error) from error
    if cancelled:
        raise asyncio.CancelledError()
    assert error is not None
    raise error


async def run_moves(plan: MovePlan, *, queue, drive: DriveMover) -> list[str]:
    """Move every planned item; returns their new account paths.

    With several items, a failure part-way raises PartialError naming what was done.
    """
    moved: list[str] = []
    with hold_pairs(queue, touched_pairs(plan.scope, plan.touched)):
        for n, item in enumerate(plan.items):
            try:
                await _move_one(plan, item, drive)
            except Exception as e:
                if len(plan.items) == 1:
                    raise
                raise PartialError(
                    e,
                    done=[i.step.src for i in plan.items[:n]],
                    failed=item.step.src,
                    not_done=[i.step.src for i in plan.items[n + 1 :]],
                ) from e
            moved.append(item.step.dst)
    return moved
