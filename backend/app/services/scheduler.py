import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import as_utc, utcnow
from app.database.backup import backup_database
from app.models import Account, SyncPair
from app.models.enums import AccountStatus
from app.paths import Layout
from app.services.app_settings import get_setting
from app.services.logs import prune_job_logs
from app.services.sync.backups import prune_backups
from app.services.sync.engine import is_eligible

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        queue,
        accounts,
        layout: Layout,
        catalog=None,
        clock=utcnow,
    ) -> None:
        self.session_factory = session_factory
        self.queue = queue
        self.accounts = accounts
        self.layout = layout
        self.catalog = catalog
        self.clock = clock
        self.aps = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        self.aps.start()
        self.aps.add_job(
            self.daily_maintenance,
            "cron",
            hour=3,
            minute=0,
            id="daily",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )
        self.aps.add_job(
            self.refresh_quotas,
            "interval",
            hours=1,
            id="quotas",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )
        if self.catalog is not None:
            self.aps.add_job(
                self.refresh_catalogs,
                "interval",
                minutes=10,
                id="catalogs",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=600,
            )
        self.reload()

    def shutdown(self) -> None:
        if self.aps.running:
            self.aps.shutdown(wait=False)

    def reload(self) -> None:
        """Make the interval jobs match the database. Safe before start()."""
        wanted: set[str] = set()
        with self.session_factory() as db:
            for pair in db.scalars(select(SyncPair)):
                account = db.get(Account, pair.account_id)
                if not (is_eligible(pair, account) and pair.schedule_interval_s):
                    continue
                job_id = f"pair-{pair.id}"
                wanted.add(job_id)
                job = self.aps.get_job(job_id)
                if job is None or job.trigger.interval.total_seconds() != pair.schedule_interval_s:
                    self.aps.add_job(
                        self.tick,
                        "interval",
                        seconds=pair.schedule_interval_s,
                        args=[pair.id],
                        id=job_id,
                        replace_existing=True,
                        coalesce=True,
                        max_instances=1,
                    )
        for job in self.aps.get_jobs():
            if job.id.startswith("pair-") and job.id not in wanted:
                job.remove()

    async def tick(self, pair_id: int) -> None:
        # async on purpose: AsyncIOScheduler runs coroutines on the event loop (plain functions
        # would run in a worker thread, where the queue cannot create tasks).
        with self.session_factory() as db:
            pair = db.get(SyncPair, pair_id)
            if pair is None:
                return
            account = db.get(Account, pair.account_id)
            if not is_eligible(pair, account):
                return
            if pair.next_retry_at is not None and self.clock() < as_utc(pair.next_retry_at):
                return
        self.queue.request(pair_id, "SCHEDULE")

    async def refresh_quotas(self) -> None:
        with self.session_factory() as db:
            ids = [
                a.id
                for a in db.scalars(
                    select(Account).where(Account.status == AccountStatus.CONNECTED)
                )
            ]
        for account_id in ids:
            try:
                await self.accounts.refresh_quota(account_id)
            except Exception:
                log.exception("quota refresh failed for account %s", account_id)

    async def refresh_catalogs(self) -> None:
        # Each pair's listing is refreshed when it is more than an hour old.
        await self.catalog.refresh_stale()

    def daily_maintenance(self) -> None:
        today = self.clock().date()
        try:
            backup_database(self.layout.db_file, self.layout.db_backups, today=today)
        except Exception:
            log.exception("daily database backup failed")
        try:
            with self.session_factory() as db:
                removed = prune_backups(self.layout, db, today)
            if removed:
                log.info("removed %s expired backup folders", removed)
        except Exception:
            log.exception("backup pruning failed")
        try:
            with self.session_factory() as db:
                days = int(get_setting(db, "log_retention_days"))  # type: ignore[arg-type]
            gone = prune_job_logs(self.layout, days, time.time())
            if gone:
                log.info("removed %s old job logs", gone)
        except Exception:
            log.exception("job log pruning failed")
