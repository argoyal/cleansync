import asyncio
import json
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import as_utc, utcnow
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

    def is_stale(self, pair_id: int, max_age_s: float = STALE_AFTER_S) -> bool:
        cat = self.load(pair_id)
        if cat is None:
            return True
        return (self.clock() - as_utc(cat.refreshed_at)).total_seconds() > max_age_s

    # ---- refreshing ----------------------------------------------------------------------
    async def refresh(self, pair_id: int) -> Catalog | None:
        lock = self._locks.setdefault(pair_id, asyncio.Lock())
        if lock.locked():
            async with lock:  # a listing is already running: use its result
                return self.load(pair_id)
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._max_parallel)
        async with lock, self._sem:
            return await self._refresh(pair_id)

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
            account = db.get(Account, pair.account_id)
            if account.status != AccountStatus.CONNECTED:
                return self.load(pair_id)
            account_id, remote_path = account.id, pair.remote_path
            target = remote_target(account.remote_name, remote_path)
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
            entries = []  # the Drive folder does not exist yet (first sync creates it)
        elif unreadable:
            self.last_error[pair_id] = "rclone printed a listing DriveSync could not read"
            log.warning(
                "pair %s: %s unreadable listing lines; kept the old listing", pair_id, unreadable
            )
            return self.load(pair_id)
        elif remote_path:
            root_id = await self._stat_id(target)
        catalog = Catalog.build(pair_id, self.clock(), root_id, entries)
        await asyncio.to_thread(self.store.save, catalog)
        self.last_error.pop(pair_id, None)
        self.bus.publish("catalog", {"pair_id": pair_id, "account_id": account_id})
        for listener in self.listeners:
            try:
                listener(pair_id)
            except Exception:
                log.exception("catalog listener failed for pair %s", pair_id)
        return catalog

    async def _stat_id(self, target: str) -> str | None:
        out: list[str] = []
        res = await self._rclone(lsjson_args(self.env, target, stat=True), out.append)
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
        return self.load(pair_id)  # keep showing the last good listing

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
            self._spawn(pair_id)
        else:
            loop.call_soon_threadsafe(self._spawn, pair_id)

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
        if changed or self.load(pair_id) is None:
            self.request_refresh(pair_id)

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
            if self.is_stale(pair_id, max_age_s):
                try:
                    await self.refresh(pair_id)
                except Exception:
                    log.exception("hourly Drive listing of pair %s failed", pair_id)

    async def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
