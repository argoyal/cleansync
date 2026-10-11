"""What the API, the schedule and startup ask of mail (spec §4): set up, start, check now,
status. Jobs run on the MailQueue, one at a time per account."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import as_utc, utcnow
from app.models import Account, AccountService, MailJob, MailSyncState
from app.models.enums import (
    AccountStatus,
    JobState,
    MailJobKind,
    MailPhase,
    ServiceKind,
    ServiceState,
)
from app.paths import Layout
from app.services.accounts.services import (
    mail_needs_reconnect,
    service_state,
    set_service_state,
)
from app.services.app_settings import get_setting
from app.services.events.bus import EventBus, record_event
from app.services.google.client import RateLimited
from app.services.google.quota import ApiBudget, BudgetReached
from app.services.mail.adapter import MailAdapter, MailApiError, MailAuthError
from app.services.mail.backup import GB, BackupRun, MailBackup
from app.services.mail.index import count_messages, shown_failures, stored_bytes, sync_state
from app.services.mail.jobs import finish_mail_job, settle_mail_jobs, start_mail_job
from app.services.mail.queue import MailQueue
from app.services.queue import MISSING_DISK_MSG, is_stopped, stop_message

log = logging.getLogger(__name__)
NOT_SET_UP_MSG = "Mail isn't set up for this account yet. Set up mail backup first."
RECONNECT_MSG = "Reconnect this Google account first. Nothing was changed."
BACKUP_KINDS = (MailJobKind.BACKUP, MailJobKind.CHECK, MailJobKind.RECHECK)
JOB_KIND = {
    MailPhase.INITIAL: MailJobKind.BACKUP,
    MailPhase.INCREMENTAL: MailJobKind.CHECK,
    MailPhase.RECHECK: MailJobKind.RECHECK,
}


class MailError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class SetupInfo:
    total: int
    size_bytes: int | None


class MailService:
    def __init__(
        self,
        *,
        layout: Layout,
        session_factory: sessionmaker[Session],
        bus: EventBus,
        accounts,
        adapters,
        budget: ApiBudget,
        get_sync_queue: Callable[[], object],
        clock=utcnow,
        disk_free=None,
    ) -> None:
        self.layout = layout
        self.sf = session_factory
        self.bus = bus
        self.accounts = accounts
        self.adapters = adapters
        self.budget = budget
        self.clock = clock
        self._get_sync_queue = get_sync_queue
        extra = {"disk_free": disk_free} if disk_free is not None else {}
        self.backup = MailBackup(
            layout=layout, session_factory=session_factory, bus=bus, clock=clock, **extra
        )
        # Plan 4 adds "offload", "empty_trash" and "restore".
        self.actions: dict[str, Callable[[int, dict, str], Awaitable[None]]] = {
            "backup": self._backup
        }
        self.queue = MailQueue(self.handle, is_stopped=self.stopped, settle=self.settle)
        self.tick_hooks: list[Callable[[], None]] = []  # Plan 4: waiting offloads

    # ---- small helpers -------------------------------------------------------------------
    async def stopped(self) -> bool:
        """The missing-disk stop, the same one Drive uses (checks the disk now)."""
        return await is_stopped(self._get_sync_queue())

    def stop_message(self) -> str | None:
        return stop_message(self._get_sync_queue())

    def adapter_for(self, account: Account) -> MailAdapter:
        return self.adapters.for_account(account)

    def settle(self, account_id: int) -> None:
        settle_mail_jobs(self.sf, self.bus, account_id)

    async def handle(self, account_id: int, action: str, args: dict, trigger: str) -> None:
        await self.actions[action](account_id, args, trigger)

    async def ready(self, account_id: int, states=(ServiceState.ACTIVE,)) -> Account:
        """Refuse, changing nothing, unless the disk is there, the login works and Mail is
        in one of `states`."""
        if await self.stopped():
            raise MailError(self.stop_message() or MISSING_DISK_MSG, 503)
        with self.sf() as db:
            acc = db.get(Account, account_id)
            if acc is None:
                raise MailError("account not found", 404)
            state = service_state(db, account_id, ServiceKind.MAIL)
        if acc.status != AccountStatus.CONNECTED or state == ServiceState.NEEDS_RECONNECT:
            raise MailError(RECONNECT_MSG)
        if state not in states:
            raise MailError(NOT_SET_UP_MSG)
        return acc

    async def _auth_lost(self, account_id: int, e: MailAuthError) -> None:
        with self.sf() as db:
            mail_needs_reconnect(db, account_id, str(e), permission_gone=not e.login)
        if e.login:
            await self.accounts.mark_needs_reauth(account_id, str(e))

    # ---- set up and start ------------------------------------------------------------------
    async def setup_info(self, account_id: int) -> SetupInfo:
        acc = await self.ready(account_id, (ServiceState.SETTING_UP, ServiceState.ACTIVE))
        try:
            info = await self.adapter_for(acc).profile()
        except MailAuthError as e:
            await self._auth_lost(account_id, e)
            raise MailError(str(e)) from None
        except (MailApiError, BudgetReached, RateLimited) as e:
            raise MailError(str(e), 502) from None
        return SetupInfo(info.total, await self.accounts.storage_outside_drive(account_id))

    async def start(self, account_id: int) -> str:
        acc = await self.ready(account_id, (ServiceState.SETTING_UP, ServiceState.ACTIVE))
        with self.sf() as db:
            set_service_state(db, account_id, ServiceKind.MAIL, ServiceState.ACTIVE)
            sync_state(db, account_id)
            record_event(
                db,
                self.bus,
                kind="mail_started",
                message=f"Mail backup for {acc.email} started",
                account_id=account_id,
            )
        self.bus.publish("account", {"account_id": account_id})
        return self.queue.request(account_id, "backup", trigger="SETUP")

    async def check_now(self, account_id: int) -> str:
        await self.ready(account_id)
        return self.queue.request(account_id, "backup", trigger="MANUAL")

    # ---- the backup job ----------------------------------------------------------------
    async def _backup(self, account_id: int, args: dict, trigger: str) -> None:
        with self.sf() as db:
            acc = db.get(Account, account_id)
            if (
                acc is None
                or acc.status != AccountStatus.CONNECTED
                or service_state(db, account_id, ServiceKind.MAIL) != ServiceState.ACTIVE
            ):
                return
            phase = sync_state(db, account_id).phase
            floor = int(get_setting(db, "mail_disk_floor_gb")) * GB  # type: ignore[arg-type]
            job = start_mail_job(db, self.layout, self.bus, account_id, JOB_KIND[phase], trigger)
        r: BackupRun | None = None
        try:
            r = BackupRun(
                account_id=account_id,
                email=acc.email,
                adapter=self.adapter_for(acc),
                job_id=job.id,
                floor_bytes=floor,
                is_stopped=self.stopped,
                log_path=job.log_path,
            )
            state, message = await self.backup.run(r)
        except Exception as e:
            log.exception("mail backup for account %s failed", account_id)
            state, message = JobState.FAILED, f"Something went wrong: {e}"
            # Counts as a run: the next try waits for the interval, not the next minute.
            try:
                with self.sf() as db:
                    st = sync_state(db, account_id)
                    st.last_run_at = self.clock()
                    st.last_error = message
                    db.commit()
            except Exception:
                log.exception("could not note the failed mail run of account %s", account_id)
        done = r.downloaded if r else 0
        size = r.bytes if r else 0
        with self.sf() as db:
            finish_mail_job(db, self.bus, job.id, state, message, done=done, bytes_=size)
            if state != JobState.SUCCESS or done or (r is not None and r.failed):
                level = {JobState.SUCCESS: "info", JobState.PAUSED: "warning"}.get(state, "error")
                record_event(
                    db,
                    self.bus,
                    kind=f"mail_{state.lower()}",
                    level=level,
                    message=f"Mail backup for {acc.email}: {message}",
                    account_id=account_id,
                    data={"mail_job_id": job.id},
                )
        if r is not None and r.login_lost:
            await self.accounts.mark_needs_reauth(account_id, message)
        self.budget.flush()

    # ---- the schedule ------------------------------------------------------------------
    async def tick(self) -> None:
        """Once a minute: start a check for each account whose interval has passed."""
        for hook in self.tick_hooks:
            try:
                hook()
            except Exception:
                log.exception("a mail tick hook failed")
        if self.budget.reached():
            return  # the day's budget is used up: wait for the next UTC day (spec §7)
        if await self.stopped():
            return  # the disk is missing: the queue would only drop the requests
        now = self.clock()
        with self.sf() as db:
            interval = timedelta(seconds=int(get_setting(db, "mail_check_interval_s")))  # type: ignore[arg-type]
            rows = db.execute(
                select(Account.id, MailSyncState.last_run_at)
                .join(AccountService, AccountService.account_id == Account.id)
                .outerjoin(MailSyncState, MailSyncState.account_id == Account.id)
                .where(
                    AccountService.kind == ServiceKind.MAIL,
                    AccountService.state == ServiceState.ACTIVE,
                    Account.status == AccountStatus.CONNECTED,
                )
            ).all()
        for account_id, last in rows:
            if last is None or now - as_utc(last) >= interval:
                self.queue.request(account_id, "backup")

    async def stop(self) -> None:
        await self.queue.stop()
        self.budget.flush()

    # ---- what the Mail page shows ------------------------------------------------------
    def status(self, account_id: int) -> dict:
        with self.sf() as db:
            acc = db.get(Account, account_id)
            if acc is None:
                raise MailError("account not found", 404)
            st = db.get(MailSyncState, account_id)
            job = db.scalar(
                select(MailJob)
                .where(MailJob.account_id == account_id, MailJob.kind.in_(BACKUP_KINDS))
                .order_by(MailJob.id.desc())
                .limit(1)
            )
            stored = count_messages(db, account_id)
            size = stored_bytes(db, account_id)
            failed = [
                {"provider_id": f.provider_id, "attempts": f.attempts, "last_error": f.last_error}
                for f in shown_failures(db, account_id)
            ]
            service = service_state(db, account_id, ServiceKind.MAIL)
        running = self.queue.is_running(account_id)
        time_left = None
        if (
            running
            and job is not None
            and job.state == JobState.RUNNING
            and st is not None
            and st.phase == MailPhase.INITIAL
            and st.total
            and job.done > 0
        ):
            elapsed = max((self.clock() - as_utc(job.started_at)).total_seconds(), 1.0)
            time_left = int(max(st.total - stored, 0) / (job.done / elapsed))
        return {
            "account_id": account_id,
            "email": acc.email,
            "service": service,
            "phase": st.phase if st else None,
            "stored": stored,
            "stored_bytes": size,
            "total": st.total if st else None,
            "running": running,
            "waiting": self.queue.is_busy(account_id) and not running,
            "time_left_s": time_left,
            "last_run_at": st.last_run_at if st else None,
            "last_error": st.last_error if st else None,
            "job": None
            if job is None
            else {
                "id": job.id,
                "kind": job.kind,
                "state": job.state,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
                "done": job.done,
                "message": job.message,
            },
            "failed": failed,
            "budget_reached": self.budget.reached(),
        }

    def overview(self) -> list[dict]:
        with self.sf() as db:
            ids = list(
                db.scalars(
                    select(AccountService.account_id).where(
                        AccountService.kind == ServiceKind.MAIL,
                        AccountService.state.in_(
                            [ServiceState.ACTIVE, ServiceState.NEEDS_RECONNECT, ServiceState.PAUSED]
                        ),
                    )
                )
            )
        return [self.status(a) for a in sorted(ids)]
