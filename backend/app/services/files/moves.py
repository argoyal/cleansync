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
from dataclasses import dataclass, field

from app.models import Account, SyncPair
from app.models.enums import AccountStatus
from app.paths import Layout
from app.services.files import ops
from app.services.files.ops import FileOpError, MoveStep
from app.services.files.scope import Scope, join_rel
from app.services.rclone.commands import RcloneEnv, lsjson_args, moveto_args, remote_target
from app.services.rclone.errors import Failure, classify_failure
from app.services.rclone.runner import RunResult, run_rclone

log = logging.getLogger(__name__)

BUSY_MSG = "this sync pair is busy; try again when the current job finishes"
DISABLED_MSG = "database integrity check failed; restore a backup (see docs/backup-recovery.md)"
AUTH_REASON = "Google says the login expired or was revoked"


def _drive_error(res: RunResult, what: str) -> str:
    detail = "; ".join(res.error_tail[-2:]) or f"rclone exited with {res.exit_code}"
    return f"Google Drive refused to {what}: {detail}"


class DriveMover:
    """Server-side moves and existence checks on an account's Google Drive."""

    def __init__(self, *, layout: Layout, env: RcloneEnv, accounts, runner=run_rclone) -> None:
        self.layout = layout
        self.env = env
        self.accounts = accounts
        self.runner = runner

    async def _rclone(self, args: list[str], out: list[str] | None = None) -> RunResult:
        return await self.runner(
            args,
            self.env.env(),
            log_path=self.layout.logs_dir / "rclone-fileops.log",
            on_stdout=out.append if out is not None else None,
            secrets=[self.env.config_pass],
        )

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
            if res.exit_code in (3, 4) or any("not found" in ln.lower() for ln in res.error_tail):
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
    pairs: dict[int, SyncPair] = field(default_factory=dict)

    @property
    def drive_pairs(self) -> set[int]:
        return {p.step.pair.id for p in self.items if p.src_target and p.step.pair}

    @property
    def touched(self) -> list[str]:
        return [r for p in self.items for r in (p.step.src, p.step.dst)]


def _target(account: Account, pair: SyncPair, prel: str) -> str:
    return remote_target(account.remote_name, join_rel(pair.remote_path, prel))


async def prepare_moves(
    scope: Scope, steps: list[MoveStep], *, queue, catalog, drive: DriveMover
) -> MovePlan:
    """Decide which items move on Drive too, and check everything before anything moves."""
    plan = MovePlan(scope, [_Planned(s) for s in steps])
    for s in steps:
        if s.pair is not None:
            plan.pairs[s.pair.id] = s.pair
    if getattr(queue, "disabled_reason", None) is not None:
        raise FileOpError(DISABLED_MSG, 503)
    for pid in plan.pairs:
        if queue.is_busy(pid):
            raise FileOpError(BUSY_MSG)
    account = scope.account
    listings = {
        pid: await asyncio.to_thread(catalog.load, pid) for pid in plan.pairs
    }  # pair id -> Drive listing (or None)
    for item in plan.items:
        pair = item.step.pair
        if pair is None:
            continue
        listing = listings[pair.id]
        src_prel, dst_prel = item.step.src_prel, item.step.dst_prel
        src_target = _target(account, pair, src_prel)
        connected = account.status == AccountStatus.CONNECTED
        if listing is not None:
            on_drive = src_prel in listing.files
        else:
            # No listing yet: ask Drive (needs a working login).
            on_drive = connected and await drive.exists(account, src_target)
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


async def run_moves(plan: MovePlan, *, queue, drive: DriveMover) -> list[str]:
    """Move every planned item; returns their new account paths."""
    account = plan.scope.account
    moved: list[str] = []
    with contextlib.ExitStack() as held:
        for pid in plan.drive_pairs:
            try:
                held.enter_context(queue.hold(pid))
            except RuntimeError:
                raise FileOpError(BUSY_MSG) from None
        for item in plan.items:
            if item.src_target and item.dst_target:
                await drive.move(account, item.src_target, item.dst_target)
            try:
                await asyncio.to_thread(ops.apply_local, plan.scope, item.step)
            except Exception:
                if item.src_target and item.dst_target:
                    try:
                        await drive.move(account, item.dst_target, item.src_target)
                    except Exception:
                        log.exception(
                            "could not undo the Google Drive move of %s after the local move "
                            "failed; the next sync will reconcile it",
                            item.step.src,
                        )
                raise
            moved.append(item.step.dst)
    return moved
