import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import utcnow
from app.models import Account, SyncJob, SyncPair
from app.models.enums import AccountStatus, JobKind
from app.paths import Layout
from app.services.catalog.model import Catalog, CatalogEntry, parse_item, valid_id
from app.services.catalog.store import CatalogStore
from app.services.events.bus import EventBus
from app.services.rclone.commands import RcloneEnv, lsjson_args, remote_target
from app.services.rclone.errors import Failure, classify_failure
from app.services.rclone.runner import RunResult, run_rclone

log = logging.getLogger(__name__)
_REAL_RUNS = (JobKind.BISYNC, JobKind.SYNC, JobKind.RESYNC)
STALE_AFTER_S = 3600
MIN_GAP_S = 60  # triggers closer than this to the last good listing wait


class CatalogService:
    """Keeps each pair's Drive listing fresh. One refresh per pair at a time."""

    def __init__(
        self,
        *,
        layout: Layout,
        session_factory: sessionmaker[Session],
        bus: EventBus,
        rclone_env: RcloneEnv,
        accounts,
        store: CatalogStore | None = None,
        runner=run_rclone,
        clock=utcnow,
        max_parallel: int = 2,
        min_gap_s: float = MIN_GAP_S,
    ) -> None:
        self.layout = layout
        self.session_factory = session_factory
        self.bus = bus
        self.env = rclone_env
        self.accounts = accounts
        self.runner = runner
        self.clock = clock
        self.store = store or CatalogStore(layout.catalog_dir)
        self.last_error: dict[int, str] = {}
        # Called with a pair id after its listing changed (e.g. to drop cached statuses).
        self.listeners: list[Callable[[int], None]] = []
        self._max_parallel = max_parallel
        self._min_gap = min_gap_s
        self._dirty: set[int] = set()  # a trigger arrived while the pair was busy or too fresh
        self._delayed: dict[int, asyncio.Task] = {}
        self._last_ok: dict[int, object] = {}  # pair id -> datetime of the last good listing
        self._last_try: dict[int, object] = {}
        self._forgotten: set[int] = set()
        self._sem: asyncio.Semaphore | None = None
        self._locks: dict[int, asyncio.Lock] = {}
        self._seen_job: dict[int, int] = {}
        self._tasks: set[asyncio.Task] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- reading -------------------------------------------------------------------------
    def load(self, pair_id: int) -> Catalog | None:
        return self.store.load(pair_id)

    def is_refreshing(self, pair_id: int) -> bool:
        lock = self._locks.get(pair_id)
        return lock is not None and lock.locked()

    def _age_of_listing(self, pair_id: int) -> float | None:
        """Seconds since the last good listing, without parsing the file."""
        ok = self._last_ok.get(pair_id)
        if ok is None:
            try:
                mtime = self.store.path(pair_id).stat().st_mtime
            except OSError:
                return None
            ok = datetime.fromtimestamp(mtime, UTC)
        return (self.clock() - ok).total_seconds()

    def is_stale(self, pair_id: int, max_age_s: float = STALE_AFTER_S) -> bool:
        age = self._age_of_listing(pair_id)
        return age is None or age > max_age_s

    def forget(self, pair_id: int) -> None:
        """The pair was deleted: drop its state and never save a listing for it again."""
        self._forgotten.add(pair_id)
        self._dirty.discard(pair_id)
        self._last_ok.pop(pair_id, None)
        self._last_try.pop(pair_id, None)
        self.last_error.pop(pair_id, None)

    # ---- refreshing ----------------------------------------------------------------------
    async def refresh(self, pair_id: int) -> Catalog | None:
        lock = self._locks.setdefault(pair_id, asyncio.Lock())
        if lock.locked():
            async with lock:  # a listing is already running: use its result
                return await asyncio.to_thread(self.load, pair_id)
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._max_parallel)
        try:
            async with lock, self._sem:
                return await self._refresh(pair_id)
        finally:
            if pair_id in self._dirty:  # a trigger came in meanwhile: one more pass
                self._schedule(pair_id)

    async def _rclone(self, args: list[str], on_stdout: Callable[[str], None]) -> RunResult:
        return await self.runner(
            args,
            self.env.env(),
            log_path=self.layout.logs_dir / "rclone-catalog.log",
            on_stdout=on_stdout,
            secrets=[self.env.config_pass],
        )

    async def _refresh(self, pair_id: int) -> Catalog | None:
        with self.session_factory() as db:
            pair = db.get(SyncPair, pair_id)
            if pair is None:
                return None
            self._forgotten.discard(pair_id)  # the id exists again
            account = db.get(Account, pair.account_id)
            if account.status != AccountStatus.CONNECTED:
                return await asyncio.to_thread(self.load, pair_id)
            account_id, remote_path = account.id, pair.remote_path
            target = remote_target(account.remote_name, remote_path)
        self._last_try[pair_id] = self.clock()
        entries: list[CatalogEntry] = []
        unreadable = 0

        def on_stdout(line: str) -> None:
            # rclone prints one JSON object per line between "[" and "]".
            nonlocal unreadable
            text = line.strip().rstrip(",")
            if not text.startswith("{"):
                return
            try:
                item = json.loads(text)
            except ValueError:
                unreadable += 1
                return
            entry = parse_item(item) if isinstance(item, dict) else None
            if entry is not None:
                entries.append(entry)

        res = await self._rclone(
            lsjson_args(
                self.env,
                target,
                recursive=True,
                fast_list=True,
                metadata=True,
                show_all_gdocs=True,
            ),
            on_stdout,
        )
        root_id: str | None = "root" if not remote_path else None
        if res.exit_code != 0:
            if not any("directory not found" in ln.lower() for ln in res.error_tail):
                return await self._failed(pair_id, account_id, res)
            # Only an empty Drive folder if the folder itself is confirmed missing.
            stat, _ = await self._stat(target)
            if stat.exit_code == 0 or not _not_found(stat.error_tail):
                return await self._failed(pair_id, account_id, res)
            entries = []  # the Drive folder does not exist yet (first sync creates it)
        elif unreadable:
            self.last_error[pair_id] = "rclone printed a listing CleanSync could not read"
            log.warning(
                "pair %s: %s unreadable listing lines; kept the old listing", pair_id, unreadable
            )
            return await asyncio.to_thread(self.load, pair_id)
        elif remote_path:
            root_id = await self._stat_id(target)
        now = self.clock()

        def build_and_save() -> Catalog:
            catalog = Catalog.build(pair_id, now, root_id, entries)
            self.store.save(catalog)
            return catalog

        with self.session_factory() as db:
            gone = db.get(SyncPair, pair_id) is None
        if gone or pair_id in self._forgotten:  # deleted while rclone was listing
            return None
        catalog = await asyncio.to_thread(build_and_save)
        self._last_ok[pair_id] = now
        self.last_error.pop(pair_id, None)
        for listener in self.listeners:
            try:
                listener(pair_id)
            except Exception:
                log.exception("catalog listener failed for pair %s", pair_id)
        # Last: an open tab refetches the moment it hears this, so the listeners above
        # (which drop the cached statuses) must already be done.
        self.bus.publish("catalog", {"pair_id": pair_id, "account_id": account_id})
        return catalog

    async def _stat(self, target: str) -> tuple[RunResult, list[str]]:
        out: list[str] = []
        res = await self._rclone(lsjson_args(self.env, target, stat=True), out.append)
        return res, out

    async def _stat_id(self, target: str) -> str | None:
        res, out = await self._stat(target)
        if res.exit_code != 0:
            return None
        try:
            item = json.loads("\n".join(out))
        except ValueError:
            return None
        return valid_id(item.get("ID")) if isinstance(item, dict) else None

    async def _failed(self, pair_id: int, account_id: int, res: RunResult) -> Catalog | None:
        failure = classify_failure(res.exit_code, res.error_tail)
        if failure == Failure.AUTH:
            await self.accounts.mark_needs_reauth(
                account_id, "Google says the login expired or was revoked"
            )
        message = "; ".join(res.error_tail[-3:]) or f"rclone exited with {res.exit_code}"
        self.last_error[pair_id] = message
        log.warning("could not list Google Drive for pair %s: %s", pair_id, message)
        return await asyncio.to_thread(self.load, pair_id)  # keep the last good listing

    # ---- triggers ------------------------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def request_refresh(self, pair_id: int) -> None:
        """Start a refresh in the background. Safe to call from any thread."""
        loop = self._loop
        try:
            running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        loop = loop or running
        if loop is None:
            log.debug("no event loop yet; refresh of pair %s skipped", pair_id)
            return
        if running is loop:
            self._trigger(pair_id)
        else:
            loop.call_soon_threadsafe(self._trigger, pair_id)

    def _trigger(self, pair_id: int) -> None:
        """On the loop thread. Never dropped: a busy or too-fresh pair gets one more pass later."""
        if self.is_refreshing(pair_id) or pair_id in self._delayed:
            self._dirty.add(pair_id)
            return
        if self._wait_needed(pair_id) > 0:
            self._dirty.add(pair_id)
            self._schedule(pair_id)
            return
        self._spawn(pair_id)

    def _wait_needed(self, pair_id: int) -> float:
        ok = self._last_ok.get(pair_id)
        if ok is None:
            return 0.0
        return max(0.0, self._min_gap - (self.clock() - ok).total_seconds())

    def _schedule(self, pair_id: int) -> None:
        """One delayed pass per pair, run once the minimum gap since the last listing is over."""
        if pair_id in self._delayed:
            return
        task = asyncio.get_running_loop().create_task(self._delayed_pass(pair_id))
        self._delayed[pair_id] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _delayed_pass(self, pair_id: int) -> None:
        try:
            await asyncio.sleep(self._wait_needed(pair_id))
            self._dirty.discard(pair_id)
            self._delayed.pop(pair_id, None)
            await self._safe_refresh(pair_id)
        finally:
            if self._delayed.get(pair_id) is asyncio.current_task():
                del self._delayed[pair_id]

    def _spawn(self, pair_id: int) -> None:
        task = asyncio.get_running_loop().create_task(self._safe_refresh(pair_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _safe_refresh(self, pair_id: int) -> None:
        try:
            await self.refresh(pair_id)
        except Exception:
            log.exception("refreshing the Drive listing of pair %s failed", pair_id)

    def refresh_account(self, account_id: int) -> None:
        with self.session_factory() as db:
            ids = list(db.scalars(select(SyncPair.id).where(SyncPair.account_id == account_id)))
        for pair_id in ids:
            self.request_refresh(pair_id)

    def on_job_finished(self, pair_id: int) -> None:
        """SyncQueue finish listener: after a real run that changed anything, list Drive again.

        Downloads and local deletes matter too: until the listing matches the disk again,
        the browser would show those files as waiting to sync.
        """
        with self.session_factory() as db:
            job = db.scalar(
                select(SyncJob)
                .where(SyncJob.pair_id == pair_id, SyncJob.kind.in_(_REAL_RUNS))
                .order_by(SyncJob.id.desc())
                .limit(1)
            )
        if job is None or self._seen_job.get(pair_id) == job.id:
            return
        self._seen_job[pair_id] = job.id
        changed = job.kind == JobKind.RESYNC or bool(job.files_changed or job.conflicts)
        if changed or not self.store.path(pair_id).exists():
            self.request_refresh(pair_id)

    def _tried_long_ago(self, pair_id: int, max_age_s: float) -> bool:
        tried = self._last_try.get(pair_id)
        return tried is None or (self.clock() - tried).total_seconds() > max_age_s

    async def refresh_stale(self, max_age_s: float = STALE_AFTER_S) -> None:
        """Hourly safety net: catches changes made on Drive that no sync has touched yet."""
        with self.session_factory() as db:
            rows = db.execute(
                select(SyncPair.id)
                .join(Account, Account.id == SyncPair.account_id)
                .where(
                    Account.status == AccountStatus.CONNECTED,
                    SyncPair.enabled.is_(True),
                    SyncPair.paused.is_(False),
                )
                .order_by(SyncPair.id)
            ).all()
        for (pair_id,) in rows:
            if self.is_stale(pair_id, max_age_s) and self._tried_long_ago(pair_id, max_age_s):
                try:
                    await self.refresh(pair_id)
                except Exception:
                    log.exception("hourly Drive listing of pair %s failed", pair_id)

    async def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _not_found(lines: list[str]) -> bool:
    return any("not found" in ln.lower() for ln in lines)
