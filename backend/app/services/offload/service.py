"""Offload (spec §7.2, §7.2a): keep files on this server, remove them from Google Drive (into
Drive's trash), and stop syncing them. Manual only: nothing here runs by itself.

Each item, with its pair held so no sync runs meanwhile:
  1. list it on Drive (IDs, sizes, MD5s);
  2. check the copy here matches Drive, file for file;
  3. record it and leave it out of sync (the pair's filter file);
  4. delete exactly the checked files on Drive, into the trash;
  5. check every recorded ID is in the trash, with the size and MD5 that were checked.
Afterwards one sync per pair, which is a safe resync because the filter changed (§6.4).
"""

import asyncio
import hashlib
import json
import logging
import secrets
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pathsafe import UnsafePath, safe_join
from app.models import Account, Offload, SyncJob, SyncPair
from app.models.enums import AccountStatus, Direction, JobKind, JobState, Trigger
from app.models.enums import OffloadState as S
from app.services.accounts.service import is_pretend
from app.services.catalog.model import valid_id
from app.services.drive_api.tokens import DriveApiError, DriveAuthError
from app.services.events.bus import record_event
from app.services.files.scope import join_rel, load_scope, parent_rel, to_account_rel
from app.services.files.status import load_overlay
from app.services.offload import store
from app.services.offload.plan import (
    ALREADY_MSG,
    BUSY_MSG,
    ONE_WAY_MSG,
    RECONNECT_MSG,
    WAITING_MSG,
    PlanItem,
    plan_offload,
)
from app.services.queue import is_stopped, stop_message
from app.services.rclone.commands import (
    UnsafeArgument,
    delete_args,
    deletefile_args,
    files_from_raw_text,
    offload_check_args,
    offload_list_args,
    remote_target,
    rmdirs_args,
)
from app.services.rclone.errors import Failure, classify_failure
from app.services.rclone.parse import CheckSummary, parse_check_combined
from app.services.rclone.runner import RunResult
from app.services.sync.engine import WAITING_FOR_USER, is_eligible
from app.services.sync.filters import refresh_pair_filters
from app.services.sync.pairs import pair_local_dir

log = logging.getLogger(__name__)
MAX_BATCHES = 20  # finished batches kept in memory for the page that started them
NOTE_NAMES = 50
TRASH_CHECKS_AT_ONCE = 4
AUTH_REASON = "Google says the login expired or was revoked"
DONE_MSG = "Removed from Google Drive (it is in Drive's trash). Kept on this server."
STOPPED_BEFORE_DELETE = (
    "Syncing stopped before anything was removed from Google Drive. Use Restore to Drive to undo."
)
NOT_RUN_MSG = "DriveSync stopped before this item ran, so nothing was removed."
EMPTY_FOLDERS_STAY = "Syncing stopped, so its empty folders may still be on Google Drive."
RESTORE_ONE_WAY_MSG = (
    "Restore to Drive works only in folders that sync both ways. Make this folder sync both "
    "ways first."
)
EMPTYING_MSG = "DriveSync is emptying these from the trash right now. Try again when it's done."
RESTORING_NO_EMPTY_MSG = (
    "This is going back to Google Drive, so its copies in Drive's trash are kept."
)
WAITING_NOTE = (
    "Goes back to Google Drive at the next sync. Syncing is off for this folder (or its "
    "Google account needs reconnecting), so turn it on first."
)


class OffloadError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class DriveFile:
    path: str  # relative to the item's parent folder, as rclone lists it
    size: int
    md5: str | None
    id: str | None


@dataclass
class BatchItem:
    path: str  # account path
    state: str = "waiting"  # waiting | running | done | attention | failed
    message: str | None = None
    offload_id: int | None = None


@dataclass
class OffloadBatch:
    id: str
    account_id: int
    items: list[BatchItem] = field(default_factory=list)
    done: bool = False


def _drive_path(pair: SyncPair, prel: str) -> str:
    """The Drive path of a pair path ("" is the pair's own Drive folder)."""
    return join_rel(pair.remote_path, prel) if prel else pair.remote_path


def names(paths: list[str]) -> str:
    shown = ", ".join(paths[:NOTE_NAMES])
    more = len(paths) - NOTE_NAMES
    return shown + (f" and {more} more" if more > 0 else "")


def parse_listing(lines: list[str]) -> list[DriveFile] | None:
    """Files from `rclone lsjson` output, or None when any line can't be read (fail closed)."""
    files: list[DriveFile] = []
    for line in lines:
        text = line.strip().rstrip(",")
        if not text.startswith("{"):
            continue
        try:
            item = json.loads(text)
        except ValueError:
            return None
        if not isinstance(item, dict):
            return None
        if item.get("IsDir"):
            continue
        path, size = item.get("Path"), item.get("Size")
        if not isinstance(path, str) or not path or not isinstance(size, int) or size < 0:
            return None
        hashes = item.get("Hashes") if isinstance(item.get("Hashes"), dict) else {}
        md5 = hashes.get("md5") if isinstance(hashes.get("md5"), str) else None
        files.append(DriveFile(path, size, md5, valid_id(item.get("ID"))))
    return files


def _listing_problem(files: list[DriveFile], name: str, is_dir: bool, pretend: bool) -> str | None:
    """Why this Drive listing can't be trusted for a delete, or None. Fails closed."""
    if not files:
        return (
            "Google Drive has none of these files yet, so nothing was removed. "
            "Sync now, then try again."
        )
    paths = [f.path for f in files]
    if len({x.casefold() for x in paths}) != len(paths):
        # Drive allows two files with one name (or names differing only in capital letters);
        # this server may keep only one of them, so the other would have no copy here.
        return (
            "Google Drive has more than one file with the same name here (perhaps differing "
            "only in capital letters), so nothing was removed. Rename one of them on Drive, "
            "sync, then try again."
        )
    inside = all(x.startswith(name + "/") for x in paths) if is_dir else paths == [name]
    if not inside:
        return "Google Drive listed files outside this item, so nothing was removed."
    if pretend:
        return None
    if any(f.id is None for f in files):
        return (
            "Google Drive didn't give an ID for every file, so DriveSync couldn't "
            "check the trash afterwards. Nothing was removed."
        )
    if any(f.md5 is None for f in files):
        return (
            "Google Drive didn't give a checksum for every file, so DriveSync couldn't "
            "check the trash afterwards. Nothing was removed."
        )
    return None


def _md5(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def local_copy_problems(local_parent: Path, manifest: list[dict]) -> list[str]:
    """Files whose copy here is missing, or differs in size or MD5 from what Drive had."""
    bad: list[str] = []
    for entry in manifest:
        rel = str(entry.get("path", ""))
        try:
            f = safe_join(local_parent, rel)
        except UnsafePath:
            bad.append(rel)
            continue
        if f.is_symlink() or not f.is_file() or f.stat().st_size != entry.get("size"):
            bad.append(rel)
            continue
        md5 = entry.get("md5")
        if isinstance(md5, str) and md5 and _md5(f) != md5:
            bad.append(rel)
    return bad


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class OffloadService:
    def __init__(
        self,
        *,
        engine,
        get_queue: Callable[[], object],
        catalog,
        indexes,
        drive,
        allow_pretend: bool = False,
    ) -> None:
        self.engine = engine
        self.layout = engine.layout
        self.sf = engine.session_factory
        self.bus = engine.bus
        self.env = engine.env
        self.accounts = engine.accounts
        self._get_queue = get_queue
        self.catalog = catalog
        self.indexes = indexes
        self.drive = drive
        self.allow_pretend = allow_pretend
        self._batches: OrderedDict[str, OffloadBatch] = OrderedDict()
        self.emptying: set[int] = set()  # offload ids being emptied from the trash
        self._tasks: set[asyncio.Task] = set()

    @property
    def queue(self):
        return self._get_queue()

    # ---- small helpers -----------------------------------------------------------------
    def _pretend(self, account: Account) -> bool:
        return is_pretend(account, self.allow_pretend)

    def _changed(self, account_id: int | None, pair_id: int | None = None) -> None:
        self.bus.publish("offload", {"account_id": account_id, "pair_id": pair_id})

    def _invalidate(self, pair_id: int) -> None:
        if self.indexes is not None:
            self.indexes.invalidate(pair_id)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _rclone(self, job: SyncJob, args: list[str], *, on_stdout=None, on_line=None):
        return await self.engine.runner(
            args,
            self.env.env(),
            log_path=Path(job.log_path),
            on_line=on_line,
            on_stdout=on_stdout,
            secrets=[self.env.config_pass],
        )

    # ---- preview -----------------------------------------------------------------------
    async def preview(self, account_id: int, rels: list[str]) -> list[PlanItem]:
        with self.sf() as db:
            scope = load_scope(db, self.layout, account_id, create_root=False)
            if scope is None:
                raise OffloadError("account not found", 404)
            offloads = {p.id: store.open_paths(db, p.id) for p in scope.pairs}
            conflicts = {p.id: load_overlay(db, p.id, False).conflicts for p in scope.pairs}
        catalogs = {p.id: await asyncio.to_thread(self.catalog.load, p.id) for p in scope.pairs}
        busy = {p.id for p in scope.pairs if self.queue.is_busy(p.id)}
        return await asyncio.to_thread(
            plan_offload,
            scope,
            rels,
            catalogs=catalogs,
            offloads=offloads,
            conflicts=conflicts,
            busy=busy,
            connected=scope.account.status == AccountStatus.CONNECTED,
        )

    # ---- run ---------------------------------------------------------------------------
    async def start(self, account_id: int, rels: list[str]) -> OffloadBatch:
        if await is_stopped(self.queue):
            raise OffloadError(stop_message(self.queue), 503)
        plan = await self.preview(account_id, rels)
        batch = OffloadBatch(secrets.token_hex(8), account_id, [BatchItem(p.path) for p in plan])
        for bi, p in zip(batch.items, plan, strict=True):
            if p.blocked:
                bi.state, bi.message = "failed", p.blocked
        self._batches[batch.id] = batch
        while len(self._batches) > MAX_BATCHES:
            self._batches.popitem(last=False)
        self._spawn(self._run_batch(batch, plan))
        return batch

    def batch(self, batch_id: str) -> OffloadBatch | None:
        return self._batches.get(batch_id)

    async def _run_batch(self, batch: OffloadBatch, plan: list[PlanItem]) -> None:
        try:
            groups: dict[int, list[tuple[BatchItem, PlanItem]]] = {}
            for bi, p in zip(batch.items, plan, strict=True):
                if p.blocked is None and p.pair is not None:
                    groups.setdefault(p.pair.id, []).append((bi, p))
            for pair_id, items in groups.items():  # one pair after another
                if await is_stopped(self.queue):
                    for bi, _ in items:
                        bi.state, bi.message = "failed", stop_message(self.queue)
                    continue
                await self._run_pair(batch, pair_id, items)
        finally:
            # A batch is never "done" with items still waiting or running.
            for bi in batch.items:
                if bi.state == "waiting":
                    bi.state, bi.message = "failed", NOT_RUN_MSG
                elif bi.state == "running":
                    bi.state = "failed" if bi.offload_id is None else "attention"
                    bi.message = NOT_RUN_MSG if bi.offload_id is None else store.INTERRUPTED_NOTE
            batch.done = True
            self._changed(batch.account_id)

    async def _run_pair(self, batch, pair_id, items) -> None:
        queue = self.queue
        if queue.is_busy(pair_id):
            for bi, _ in items:
                bi.state, bi.message = "failed", BUSY_MSG
            return
        recorded = False
        try:
            held = queue.hold(pair_id)
            held.__enter__()
        except RuntimeError:  # a sync started a moment ago
            for bi, _ in items:
                bi.state, bi.message = "failed", BUSY_MSG
            return
        except Exception as e:
            log.exception("could not hold pair %s for an offload", pair_id)
            for bi, _ in items:
                bi.state = "failed"
                bi.message = f"DriveSync couldn't start this offload, so nothing was removed: {e}"
            return
        try:
            for bi, p in items:
                if await is_stopped(queue):
                    bi.state, bi.message = "failed", stop_message(queue)
                    continue
                bi.state = "running"
                self._changed(batch.account_id, pair_id)
                try:
                    recorded |= await self._offload_one(bi, p)
                except Exception as e:  # never leave a row half-way
                    log.exception("offload of %s failed unexpectedly", p.path)
                    bi.state, bi.message = "failed", f"Something went wrong: {e}"
                    if self._settle_crashed(bi):
                        recorded = True
                        bi.state = "attention"
                        bi.message = f"Something went wrong: {e}. {store.INTERRUPTED_NOTE}"
                self._changed(batch.account_id, pair_id)
        finally:
            held.__exit__(None, None, None)
        if recorded:
            self.catalog.request_refresh(pair_id)
            # The filter changed, so this sync runs as a safe resync (§6.4): it never deletes.
            queue.request(pair_id, Trigger.OFFLOAD)

    def _settle_crashed(self, bi: BatchItem) -> bool:
        if bi.offload_id is None:
            return False
        with self.sf() as db:
            off = db.get(Offload, bi.offload_id)
            if off is not None and off.state in store.UNFINISHED_STATES:
                off.state, off.note = S.ATTENTION, store.INTERRUPTED_NOTE
                db.commit()
        return True

    async def _offload_one(self, bi: BatchItem, p: PlanItem) -> bool:
        """One item; True once a row was recorded (the pair then needs its safe resync)."""
        with self.sf() as db:
            pair = db.get(SyncPair, p.pair.id)
            if pair is None:
                bi.state, bi.message = "failed", "This synced folder was removed."
                return False
            if pair.direction != Direction.BIDIRECTIONAL:  # changed after it was planned
                bi.state, bi.message = "failed", ONE_WAY_MSG
                return False
            if pair.attention_reason in WAITING_FOR_USER:  # a sync stopped after planning
                bi.state, bi.message = "failed", WAITING_MSG
                return False
            paths = store.open_paths(db, pair.id)
            if store.covers(paths, p.prel) or (p.is_dir and store.has_inside(paths, p.prel)):
                # Another offload took it after this one was planned.
                bi.state, bi.message = "failed", ALREADY_MSG
                return False
            account = db.get(Account, pair.account_id)
            job = self.engine.start_job(db, pair, JobKind.OFFLOAD, Trigger.OFFLOAD)
            try:
                return await self._offload_steps(db, job, bi, p, pair, account)
            except Exception as e:
                db.rollback()
                if job.state == JobState.RUNNING:
                    self.engine.finish_job(db, job, JobState.FAILED, error=f"Offload failed: {e}")
                raise

    async def _offload_steps(
        self, db: Session, job: SyncJob, bi: BatchItem, p: PlanItem, pair: SyncPair, account
    ) -> bool:
        name = p.prel.rsplit("/", 1)[-1]
        parent = parent_rel(p.prel)
        remote_parent = remote_target(account.remote_name, _drive_path(pair, parent))
        target = remote_target(account.remote_name, _drive_path(pair, p.prel))
        local_parent = safe_join(pair_local_dir(self.layout, account, pair), parent)
        pretend = self._pretend(account)

        # 1. What Drive has, with the IDs and MD5s to check in the trash afterwards.
        out: list[str] = []
        res = await self._rclone(
            job,
            offload_list_args(self.env, remote_parent, name, p.is_dir),
            on_stdout=out.append,
        )
        files = parse_listing(out) if res.exit_code == 0 else None
        if files is None:
            return await self._fail(
                db,
                job,
                bi,
                account,
                res,
                "DriveSync couldn't list it on Google Drive, so nothing was removed.",
            )
        problem = _listing_problem(files, name, p.is_dir, pretend)
        if problem:
            return self._stop(db, job, bi, problem)
        # The delete names exactly the listed files, never "whatever is in the folder now":
        # a file added on Drive after this listing was never checked, so it must stay there.
        delete_list = store.delete_list_file(self.layout, job.id)
        try:
            if p.is_dir:
                list_text = files_from_raw_text([f.path[len(name) + 1 :] for f in files])
                delete_cmd = delete_args(self.env, target, delete_list)
            else:
                list_text = None
                delete_cmd = deletefile_args(self.env, target)
            rmdirs_cmd = rmdirs_args(self.env, target) if p.is_dir else None
        except UnsafeArgument:
            return self._stop(
                db,
                job,
                bi,
                "Some names on Google Drive can't be removed safely by DriveSync, so nothing "
                "was removed.",
            )

        # 2. The copy here must match Drive, file for file.
        problem = await self._check_local(
            job, local_parent, remote_parent, name, p.is_dir, {f.path for f in files}
        )
        if problem:
            return self._stop(db, job, bi, problem)

        # 3. Record it. From here on no sync touches this path.
        off = Offload(
            pair_id=pair.id,
            path=p.prel,
            is_dir=p.is_dir,
            file_count=len(files),
            total_bytes=sum(f.size for f in files),
            state=S.PENDING,
            # Filled in when the delete starts: Recovery lists only offloads that reached it.
            trashed_ids=[],
        )
        db.add(off)
        db.commit()
        bi.offload_id = off.id
        await asyncio.to_thread(
            store.write_manifest, self.layout, off.id, [asdict(f) for f in files]
        )
        if refresh_pair_filters(db, self.layout, pair):
            pair.needs_resync = True
            db.commit()
        self._invalidate(pair.id)

        # 4. Delete on Drive, into the trash: only while syncing is on (the disk is there).
        if await is_stopped(self.queue):
            self._settle(db, job, bi, account, off, S.ATTENTION, STOPPED_BEFORE_DELETE)
            return True
        off.state = S.DELETING
        off.trashed_ids = [f.id for f in files if f.id]
        db.commit()
        notes: list[str] = []
        refused: str | None = None
        try:
            if list_text is not None:
                await asyncio.to_thread(_write_text, delete_list, list_text)
            res = await self._rclone(job, delete_cmd)
        finally:
            await asyncio.to_thread(delete_list.unlink, missing_ok=True)
        if res.exit_code != 0:
            refused = "; ".join(res.error_tail[-2:]) or f"rclone exited with {res.exit_code}"
            if classify_failure(res.exit_code, res.error_tail) == Failure.AUTH:
                await self.accounts.mark_needs_reauth(account.id, AUTH_REASON)
        elif rmdirs_cmd is not None:
            if await is_stopped(self.queue):
                notes.append(EMPTY_FOLDERS_STAY)
            else:
                rm = await self._rclone(job, rmdirs_cmd)
                if rm.exit_code != 0:  # only empty folders are left: not worth a warning
                    log.info("empty folders of %s stay on Drive: %s", p.prel, rm.error_tail[-1:])

        # 5. Every file must now be in Drive's trash, and be the file that was checked.
        off.state = S.VERIFYING
        db.commit()
        missing: list[str] | None
        changed: list[str] = []
        try:
            if pretend:
                missing = []
            else:
                missing, changed = await self._not_in_trash(account.remote_name, files)
        except DriveAuthError:
            await self.accounts.mark_needs_reauth(account.id, AUTH_REASON)
            missing = None
        except DriveApiError as e:
            log.warning("could not check Drive's trash for %s: %s", p.prel, e)
            missing = None
        if missing == [] and not changed and refused is None:
            off.trash_verified = True
            if pretend:
                notes.insert(0, "Pretend account: there is no trash to check.")
            self._settle(db, job, bi, account, off, S.ACTIVE, " ".join(notes) or None)
            return True
        parts = []
        if refused:
            parts.append(f"Google Drive refused to remove some files: {refused}.")

        def shown(paths: list[str]) -> str:
            return names([to_account_rel(pair, join_rel(parent, m)) for m in paths])

        if missing:
            parts.append(f"These files are not in Google Drive's trash: {shown(missing)}.")
        if missing is None:
            parts.append("DriveSync could not check Google Drive's trash.")
        if changed:
            parts.append(
                "These files changed on Google Drive after DriveSync checked them, so the "
                "newer version is in Google Drive's trash, not on this server: "
                f"{shown(changed)}. Recover them from Google Drive's trash soon: Google "
                "empties it after 30 days."
            )
        parts.append("Everything stays on this server and out of sync.")
        if changed:
            parts.append("Once you have the newer versions back, use Restore to Drive.")
        else:
            parts.append("Use Restore to Drive to put it back on Drive.")
        self._settle(db, job, bi, account, off, S.ATTENTION, " ".join(parts))
        return True

    async def _check_local(self, job, local_parent, remote_parent, name, is_dir, listed):
        same: set[str] = set()
        same_count = 0
        other: list[str] = []
        summary = CheckSummary()

        def on_stdout(line: str) -> None:
            nonlocal same_count
            parsed = parse_check_combined(line)
            if parsed is None:
                return
            if parsed[0] == "=":
                same_count += 1
                same.add(parsed[1])
            else:
                other.append(parsed[1])

        res = await self._rclone(
            job,
            offload_check_args(self.env, local_parent, remote_parent, name, is_dir),
            on_stdout=on_stdout,
            on_line=summary.feed,
        )
        if other:
            return (
                f"The copy on this server doesn't match Google Drive ({len(other)} "
                f"differences, for example {other[0]}). Nothing was removed."
            )
        if res.exit_code != 0 or summary.fatal:
            return "DriveSync couldn't compare it with Google Drive, so nothing was removed."
        if same_count != len(listed):  # a check that compared nothing must never pass
            return (
                f"DriveSync compared {same_count} files but Google Drive lists {len(listed)}, "
                "so nothing was removed."
            )
        if same != listed:
            return (
                "DriveSync compared other files than the ones Google Drive lists, so nothing "
                "was removed."
            )
        return None

    async def _not_in_trash(
        self, remote_name: str, files: list[DriveFile]
    ) -> tuple[list[str], list[str]]:
        """Listed files that are not in Drive's trash as the file that was checked.

        Returns (missing, changed): missing are gone for good or not in the trash; changed are
        in the trash with another size or MD5 (or none), so changed on Drive meanwhile."""
        gate = asyncio.Semaphore(TRASH_CHECKS_AT_ONCE)

        async def one(f: DriveFile) -> tuple[str, str | None]:
            async with gate:
                state = await self.drive.file_state(remote_name, f.id)
            if state is None or not state.trashed:
                return f.path, "missing"
            return f.path, None if state.matches(f.size, f.md5) else "changed"

        found = await asyncio.gather(*(one(f) for f in files))
        return (
            [p for p, why in found if why == "missing"],
            [p for p, why in found if why == "changed"],
        )

    def _stop(self, db: Session, job: SyncJob, bi: BatchItem, message: str) -> bool:
        self.engine.finish_job(db, job, JobState.FAILED, error=message)
        bi.state, bi.message = "failed", message
        return False

    async def _fail(self, db, job, bi, account, res: RunResult, message: str) -> bool:
        if classify_failure(res.exit_code, res.error_tail) == Failure.AUTH:
            await self.accounts.mark_needs_reauth(account.id, AUTH_REASON)
        self.engine.finish_job(db, job, JobState.FAILED, res, error=message)
        bi.state, bi.message = "failed", message
        return False

    def _settle(self, db, job, bi, account, off: Offload, state: str, note: str | None) -> None:
        off.state, off.note = state, note
        db.commit()
        ok = state == S.ACTIVE
        self.engine.finish_job(
            db, job, JobState.SUCCESS if ok else JobState.FAILED, error=None if ok else note
        )
        bi.state = "done" if ok else "attention"
        bi.message = (DONE_MSG + (f" {note}" if note else "")) if ok else note
        pair = db.get(SyncPair, off.pair_id)
        shown = to_account_rel(pair, off.path)
        record_event(
            db,
            self.bus,
            kind="offload_done" if ok else "offload_attention",
            level="info" if ok else "warning",
            pair_id=pair.id,
            account_id=account.id,
            message=(
                f"{pair.name}: offloaded {shown} ({off.file_count} files)"
                if ok
                else f"{pair.name}: offload of {shown} needs a look. {note}"
            ),
        )
        self._invalidate(pair.id)

    # ---- Restore to Drive (spec §7.2) --------------------------------------------------
    async def restore(self, offload_id: int) -> Offload:
        """Put an offloaded item back in sync. Its sync is then a safe resync (the filter
        changed), which uploads the files again and never deletes anything."""
        queue = self.queue
        # The only await comes first: from reading the row to claiming it (RESTORING) nothing
        # yields, so Empty from trash now can't start in between.
        if await is_stopped(queue):
            raise OffloadError(stop_message(queue), 503)
        with self.sf() as db:
            off = db.get(Offload, offload_id)
            if off is None:
                raise OffloadError("offload not found", 404)
            if off.state == S.RESTORING:
                raise OffloadError("This is already going back to Google Drive.")
            if off.id in self.emptying:
                raise OffloadError(EMPTYING_MSG)
            if off.state not in (S.ACTIVE, S.ATTENTION):
                raise OffloadError("This offload is busy right now. Try again in a moment.")
            pair = db.get(SyncPair, off.pair_id)
            account = db.get(Account, pair.account_id)
            if pair.direction != Direction.BIDIRECTIONAL:
                # Only a safe resync finishes a restore, and one-way folders never run one.
                raise OffloadError(RESTORE_ONE_WAY_MSG)
            if pair.attention_reason in WAITING_FOR_USER:
                # Its sync would run as a manual one and skip the owner's look.
                raise OffloadError(WAITING_MSG)
            if queue.is_busy(pair.id):
                raise OffloadError(BUSY_MSG)
            try:
                held = queue.hold(pair.id)
                held.__enter__()
            except RuntimeError:  # a sync started a moment ago
                raise OffloadError(BUSY_MSG) from None
            try:
                off.state = S.RESTORING
                off.note = None if is_eligible(pair, account) else WAITING_NOTE
                db.commit()
                if refresh_pair_filters(db, self.layout, pair):
                    pair.needs_resync = True
                    db.commit()
                self._invalidate(pair.id)
            finally:
                held.__exit__(None, None, None)
            queue.request(pair.id, Trigger.OFFLOAD)
            record_event(
                db,
                self.bus,
                kind="offload_restore",
                pair_id=pair.id,
                account_id=account.id,
                message=f"{pair.name}: putting {to_account_rel(pair, off.path)} back on Google Drive",
            )
            self._changed(account.id, pair.id)
            return off

    def resync_done(self, pair_id: int) -> None:
        """A safe resync of the pair finished: every restore waiting on it is done.

        Marking every RESTORING row of the pair is safe only because offloads never nest
        (plan.py refuses an item inside, or holding, an open offload), so each RESTORING path
        is back in the filter on its own and this resync just uploaded it."""
        with self.sf() as db:
            rows = db.scalars(
                select(Offload).where(Offload.pair_id == pair_id, Offload.state == S.RESTORING)
            ).all()
            if not rows:
                return
            pair = db.get(SyncPair, pair_id)
            for off in rows:
                off.state, off.note = S.RESTORED, None
            db.commit()
            for off in rows:
                record_event(
                    db,
                    self.bus,
                    kind="offload_restored",
                    pair_id=pair_id,
                    account_id=pair.account_id,
                    message=f"{pair.name}: {to_account_rel(pair, off.path)} is back on Google Drive",
                )
            account_id = pair.account_id
        self.catalog.request_refresh(pair_id)
        self._invalidate(pair_id)
        self._changed(account_id, pair_id)

    # ---- Empty from trash now (spec §7.2) -----------------------------------------------
    async def empty_trash(self, offload_id: int) -> Offload:
        """Delete for good, in the background, the files this offload put in Drive's trash."""
        queue = self.queue
        # The only await comes first: from reading the row to claiming it (`emptying`) nothing
        # yields, so a Restore or a second Empty can't start in between.
        if await is_stopped(queue):
            raise OffloadError(stop_message(queue), 503)
        with self.sf() as db:
            off = db.get(Offload, offload_id)
            if off is None:
                raise OffloadError("offload not found", 404)
            if off.id in self.emptying:
                raise OffloadError("DriveSync is already emptying these from the trash.")
            if off.state == S.RESTORING:
                raise OffloadError(RESTORING_NO_EMPTY_MSG)
            if off.state != S.ACTIVE or not off.trash_verified:
                raise OffloadError(
                    "Only offloads that are done and checked in the trash can be emptied."
                )
            if off.trash_emptied:
                raise OffloadError("These are already gone from Google Drive's trash.")
            pair = db.get(SyncPair, off.pair_id)
            account = db.get(Account, pair.account_id)
            if account.status != AccountStatus.CONNECTED:
                raise OffloadError(RECONNECT_MSG)
            self.emptying.add(off.id)
        self._spawn(self._empty(offload_id))
        self._changed(account.id, pair.id)
        return off

    def _set_note(self, offload_id: int, note: str | None, emptied: bool | None = None) -> None:
        with self.sf() as db:
            off = db.get(Offload, offload_id)
            if off is None:
                return
            off.note = note
            if emptied is not None:
                off.trash_emptied = emptied
            db.commit()

    def _still_offloaded(self, offload_id: int) -> bool:
        with self.sf() as db:
            off = db.get(Offload, offload_id)
            return off is not None and off.state == S.ACTIVE

    async def _empty(self, offload_id: int) -> None:
        account_id = pair_id = None
        try:
            with self.sf() as db:
                off = db.get(Offload, offload_id)
                pair = db.get(SyncPair, off.pair_id)
                account = db.get(Account, pair.account_id)
                account_id, pair_id = account.id, pair.id
                remote, recorded = account.remote_name, set(off.trashed_ids or [])
                pretend = self._pretend(account)
                local_parent = safe_join(
                    pair_local_dir(self.layout, account, pair), parent_rel(off.path)
                )
                shown_parent = to_account_rel(pair, parent_rel(off.path))
                label = f"{pair.name}: {to_account_rel(pair, off.path)}"
            manifest = await asyncio.to_thread(store.read_manifest, self.layout, offload_id)
            if not manifest:
                return self._set_note(
                    offload_id,
                    "DriveSync lost its list of these files, so nothing was deleted from the trash.",
                )
            # Re-check the copy here first (spec §7.2): it is about to be the only one.
            changed = await asyncio.to_thread(local_copy_problems, local_parent, manifest)
            if changed:
                shown = [join_rel(shown_parent, c) for c in changed]
                return self._set_note(
                    offload_id,
                    "The copy on this server is not the same as when it was offloaded "
                    f"({names(shown)}). Nothing was deleted from the trash.",
                )
            if pretend:
                return self._set_note(
                    offload_id, "Pretend account: there is no trash to empty.", emptied=True
                )
            # Only IDs this offload recorded, each once.
            entries: dict[str, dict] = {}
            for entry in manifest:
                fid = valid_id(entry.get("id"))
                if fid is not None and fid in recorded and fid not in entries:
                    entries[fid] = entry
            unlisted = len(recorded - set(entries))
            deleted = 0
            on_drive: list[str] = []  # not in the trash any more, or changed there
            here: list[str] = []  # the copy on this server changed meanwhile
            stopped: str | None = None
            for fid, entry in entries.items():
                if await is_stopped(self.queue):
                    stopped = f"{stop_message(self.queue)} Emptying the trash stopped part-way."
                    break
                if not await asyncio.to_thread(self._still_offloaded, offload_id):
                    stopped = (
                        "This is no longer offloaded (it is going back to Google Drive), so "
                        "emptying the trash stopped."
                    )
                    break
                shown = join_rel(shown_parent, str(entry.get("path", "")))
                # A cheap last look at the copy here (the full size and MD5 check ran above):
                # it must still be there, with the size it was offloaded with.
                if await asyncio.to_thread(
                    local_copy_problems,
                    local_parent,
                    [{"path": entry.get("path"), "size": entry.get("size"), "md5": None}],
                ):
                    here.append(shown)
                    continue
                size, md5 = entry.get("size"), entry.get("md5")
                ok = (
                    isinstance(size, int)
                    and isinstance(md5, str)
                    and bool(md5)
                    # Deletes only if still in the trash with the size and MD5 offloaded.
                    and await self.drive.delete_forever(remote, fid, size=size, md5=md5)
                )
                if ok:
                    deleted += 1
                else:
                    on_drive.append(shown)
            total = len(recorded)
            all_gone = deleted == total and stopped is None
            if all_gone:
                self._set_note(offload_id, None, emptied=True)
            else:
                parts = [f"Deleted {deleted} of {total} files from Google Drive's trash for good."]
                if stopped:
                    parts.append(stopped)
                if on_drive:
                    parts.append(
                        "Kept because they are no longer in Google Drive's trash, or changed "
                        f"there after the offload (someone took them out or edited them): "
                        f"{names(on_drive)}."
                    )
                if here:
                    parts.append(
                        f"Kept because the copy changed on this server meanwhile: {names(here)}."
                    )
                if unlisted:
                    noun = "file isn't" if unlisted == 1 else "files aren't"
                    parts.append(
                        f"{unlisted} {noun} in DriveSync's saved list, so "
                        f"{'it was' if unlisted == 1 else 'they were'} left in the trash."
                    )
                self._set_note(offload_id, " ".join(parts), emptied=False)
            with self.sf() as db:
                record_event(
                    db,
                    self.bus,
                    kind="offload_trash_emptied",
                    level="info" if all_gone else "warning",
                    pair_id=pair_id,
                    account_id=account_id,
                    message=(
                        f"{label}: deleted all {total} files from Google Drive's trash"
                        if all_gone
                        else f"{label}: deleted {deleted} of {total} files from Google Drive's "
                        f"trash; {total - deleted} left in the trash"
                    ),
                )
        except DriveAuthError:
            if account_id is not None:
                await self.accounts.mark_needs_reauth(account_id, AUTH_REASON)
            self._set_note(offload_id, "The Google login needs to be reconnected. Try again then.")
        except DriveApiError as e:
            self._set_note(
                offload_id, f"Google Drive refused: {e}. Some files may still be in the trash."
            )
        except Exception as e:  # never leave it "emptying" without a word
            log.exception("emptying the trash of offload %s failed", offload_id)
            self._set_note(
                offload_id,
                f"Something went wrong: {e}. Some files may still be in the trash.",
            )
        finally:
            self.emptying.discard(offload_id)
            self._changed(account_id, pair_id)
