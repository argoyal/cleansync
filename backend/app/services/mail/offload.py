"""Mail offload and restore (spec 5): out of Gmail by filter or by pick, into Gmail's trash
(checked before and after), and back into Gmail without ever making a second copy.
Runs as mail queue actions, so never beside a backup of the account."""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import as_utc
from app.models import Account, MailMessage, MailOffload, MailOffloadItem
from app.models.enums import (
    AccountStatus,
    JobState,
    MailItemResult,
    MailJobKind,
    MailOffloadState,
    MailPhase,
    MailState,
    ServiceKind,
    ServiceState,
)
from app.services.accounts.services import service_state
from app.services.app_settings import get_setting
from app.services.events.bus import record_event
from app.services.google.client import RateLimited
from app.services.google.quota import BudgetReached
from app.services.mail.adapter import (
    GONE,
    IN_TRASH,
    MailApiError,
    MailAuthError,
    MailNotFound,
    MailSetupProblem,
    MailUnreachable,
)
from app.services.mail.backup import Paused
from app.services.mail.index import sync_state
from app.services.mail.jobs import finish_mail_job, job_log, start_mail_job, update_mail_job
from app.services.mail.offload_plan import GMAIL_TRASH_DAYS, OffloadPreview, Pick, plan_offload
from app.services.mail.search import SearchError
from app.services.mail.service import MailError, MailService
from app.services.mail.store import check_eml, eml_path
from app.services.queue import MISSING_DISK_MSG

log = logging.getLogger(__name__)
BACKUP_FIRST_MSG = (
    "The first mail backup hasn't finished yet. Offload works once every mail is backed up."
)
BACKUP_CHECKING_MSG = (
    "Wait until the mail backup has finished checking your mailbox, then try again."
)
NOTHING_MSG = "Nothing to offload with this filter."
COUNT_CHANGED_MSG = (
    "The number of mails changed since the preview (now {n:,}). Check the preview again."
)
# Why one mail stayed in Gmail (listed on the offload's page).
SKIP_NOT_IN_GMAIL = "It was no longer in Gmail."
SKIP_TOO_NEW = "It is newer than {days} days, so it stayed in Gmail."
SKIP_BAD_COPY = "The copy on this server didn't check out ({problem}), so it stayed in Gmail."
SKIP_GONE = "Gmail no longer has this mail. The copy here is kept."
SKIP_OUTSIDE_TRASH = (
    "Gmail shows it outside its trash (maybe in Spam), so CleanSync left it as it is."
)
SKIP_REFUSED = "Gmail didn't move it to the trash ({error}), so it stayed in Gmail."
SKIP_UNCHECKED = (
    "CleanSync couldn't check that it reached Gmail's trash ({error}). It shows as Deleted "
    "in Gmail after the next check; restore it from there if needed."
)
SKIP_NOT_TRASHED = "Gmail didn't put it in the trash, so it stayed in Gmail."
# Why a whole offload ended early. What it had not reached stayed in Gmail.
REST_STAYED = "What wasn't offloaded yet stayed in Gmail."
OFFLOAD_RECONNECT_MSG = f"Reconnect this Google account. {REST_STAYED}"
OFFLOAD_MAIL_OFF_MSG = f"Mail backup isn't on for this account any more. {REST_STAYED}"
OFFLOAD_TROUBLE_MSG = (
    "CleanSync couldn't reach Gmail ({error}). " + REST_STAYED + " Start a new offload to go on."
)
OFFLOAD_SLOW_MSG = (
    "Gmail asked CleanSync to slow down. The offload goes on by itself in a minute or so."
)
OFFLOAD_REFUSED_MSG = (
    "Gmail refused {n} mails in a row ({error}). " + REST_STAYED + " Start a new offload to go on."
)
REFUSED_IN_A_ROW = 5  # this many refusals in a row are Gmail's trouble, not those mails'


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _count(db: Session, off_id: int, *where) -> int:
    q = (
        select(func.count())
        .select_from(MailOffloadItem)
        .where(MailOffloadItem.offload_id == off_id, *where)
    )
    return db.scalar(q) or 0


def offload_out(db: Session, off: MailOffload, now: datetime) -> dict:
    """What the offload pages show. Gmail empties its own trash after 30 days (spec 5.4);
    CleanSync never empties it (carry #1), so trash_until is when the space comes back."""
    trash_until = (as_utc(off.created_at) + timedelta(days=GMAIL_TRASH_DAYS)).date()
    return {
        "id": off.id,
        "account_id": off.account_id,
        "filter_text": off.filter_text,
        "state": off.state,
        "total": off.total,
        "done": off.done,
        "skipped": off.skipped,
        "not_done": _count(db, off.id, MailOffloadItem.result == MailItemResult.PENDING),
        "bytes": off.bytes,
        "reason": off.reason,
        "created_at": off.created_at,
        "finished_at": off.finished_at,
        "trash_until": trash_until,
        "google_emptied": now.date() > trash_until,
    }


def summary(off: MailOffload) -> str:
    text = f"Offloaded {off.done:,} of {off.total:,} mails ({human_size(off.bytes)})"
    return text + (f"; {off.skipped:,} skipped" if off.skipped else "")


class _Refusing(Exception):
    """Gmail refused several mails in a row: stop the run instead of skipping the list."""


class MailOffloads:
    def __init__(self, mail: MailService) -> None:
        self.mail = mail
        self.sf = mail.sf
        self.layout = mail.layout
        self.bus = mail.bus
        self.clock = mail.clock
        mail.actions["offload"] = self._run
        mail.tick_hooks.append(self.resume_waiting)

    def _min_age(self, db) -> int:
        return int(get_setting(db, "mail_min_age_days"))  # type: ignore[arg-type]

    async def _ready(self, account_id: int) -> Account:
        """Spec 5.2 refusals: the disk, the login, Mail set up, and a backup that is not
        still listing the whole mailbox (the first one, or a recheck)."""
        acc = await self.mail.ready(account_id)
        with self.sf() as db:
            phase = sync_state(db, account_id).phase
        if phase == MailPhase.INITIAL:
            raise MailError(BACKUP_FIRST_MSG)
        if phase == MailPhase.RECHECK:
            raise MailError(BACKUP_CHECKING_MSG)
        return acc

    async def preview(self, account_id: int, pick: Pick) -> OffloadPreview:
        acc = await self._ready(account_id)

        def work() -> OffloadPreview:
            with self.sf() as db:
                return plan_offload(
                    db, self.layout, acc, pick, min_age_days=self._min_age(db), now=self.clock()
                )

        try:
            return await asyncio.to_thread(work)  # stats every file: off the event loop
        except SearchError as e:
            raise MailError(str(e), 422) from e

    # ---- start ---------------------------------------------------------------------------
    def _changed(self, account_id: int, off_id: int | None = None) -> None:
        self.bus.publish("mail", {"account_id": account_id, "offload_id": off_id})

    async def start(self, account_id: int, pick: Pick, confirm: int) -> int:
        """Spec 5.3: the count is worked out again now, and must be the one the owner typed."""
        preview = await self.preview(account_id, pick)  # the same refusals as the preview
        if preview.count == 0:
            raise MailError(NOTHING_MSG)
        if confirm != preview.count:
            raise MailError(COUNT_CHANGED_MSG.format(n=preview.count))
        with self.sf() as db:
            off = MailOffload(
                account_id=account_id,
                filter_text=preview.filter_text,
                state=MailOffloadState.WAITING,
                total=preview.count,
            )
            db.add(off)
            db.flush()
            db.add_all(
                MailOffloadItem(offload_id=off.id, message_id=mid) for mid in preview.message_ids
            )
            db.commit()
            off_id = off.id
            record_event(
                db,
                self.bus,
                kind="mail_offload_started",
                message=f"Offload of {preview.count:,} mails asked for ({preview.filter_text})",
                account_id=account_id,
                data={"mail_offload_id": off_id},
            )
        # Waits behind a running mail job of the account (spec 5.2), never beside it.
        self.mail.queue.request(account_id, "offload", {"offload_id": off_id}, trigger="MANUAL")
        self._changed(account_id, off_id)
        return off_id

    def resume_waiting(self) -> None:
        """Each minute: offloads paused by the budget or a rate limit (or not yet run) go on
        when they can. The budget pause lasts until the next UTC day (spec 7)."""
        if self.mail.budget.reached():
            return
        with self.sf() as db:
            rows = db.execute(
                select(MailOffload.id, MailOffload.account_id).where(
                    MailOffload.state == MailOffloadState.WAITING
                )
            ).all()
        for off_id, account_id in rows:
            self.mail.queue.request(account_id, "offload", {"offload_id": off_id})

    # ---- one mail ------------------------------------------------------------------------
    def _skip(self, off_id: int, item_id: int, reason: str, log_path: str | None) -> None:
        with self.sf() as db:
            item = db.get(MailOffloadItem, item_id)
            item.result, item.reason = MailItemResult.SKIPPED, reason
            db.get(MailOffload, off_id).skipped += 1
            db.commit()
        job_log(log_path, f"skipped mail {item_id}: {reason}")

    async def _copy_problem(self, path, size: int, sha: str) -> str | None:
        """Why the copy here can't be trusted, or None. A missing disk is not the mail's
        fault: it stops the whole run (the mails not reached stay pending), never a skip."""
        problem = await asyncio.to_thread(check_eml, path, size, sha)
        if problem and (
            await self.mail.stopped() or not await asyncio.to_thread(self.layout.mail_root.is_dir)
        ):
            raise Paused(self.mail.stop_message() or MISSING_DISK_MSG)
        return problem

    def _mark_offloaded(self, off_id: int, item_id: int, msg_id: int) -> None:
        with self.sf() as db:
            row = db.get(MailMessage, msg_id)
            row.state, row.offload_id = MailState.OFFLOADED, off_id
            db.get(MailOffloadItem, item_id).result = MailItemResult.OFFLOADED
            off = db.get(MailOffload, off_id)
            off.done += 1
            off.bytes += row.size
            db.commit()

    async def _offload_one(
        self, acc: Account, adapter, off_id: int, item_id: int, log_path
    ) -> str | None:
        """Spec 5.3, in this order: 1. the copy here checks out; 2. trash in Gmail; 3. Gmail
        shows it in the trash, then it is marked offloaded. A failed step skips this mail
        only. Trouble that is not this mail's (login, budget, Gmail unreachable, the disk) is
        raised. Returns Gmail's error when it refused to trash the mail."""
        with self.sf() as db:
            min_age = self._min_age(db)  # as set now: the owner may change it during a run
            item = db.get(MailOffloadItem, item_id)
            row = db.get(MailMessage, item.message_id)
            if row is None or row.account_id != acc.id:
                return self._skip(off_id, item_id, SKIP_NOT_IN_GMAIL, log_path)
            path = eml_path(self.layout, acc.email, row.file_path)
            state, date = row.state, row.date
            size, sha, pid, msg_id = row.file_size, row.sha256, row.provider_id, row.id
        if state == MailState.DELETED:
            # Trashed by this offload before a pause, and a check saw it in Gmail's trash
            # before this offload could: claim it, once the copy here checks out.
            problem = await self._copy_problem(path, size, sha)
            if problem:
                return self._skip(off_id, item_id, SKIP_BAD_COPY.format(problem=problem), log_path)
            return await self._check_trashed(
                adapter, off_id, item_id, msg_id, pid, log_path, outside=SKIP_OUTSIDE_TRASH
            )
        # Checked again now: the preview may be minutes or days old.
        if state != MailState.IN_MAIL:
            return self._skip(off_id, item_id, SKIP_NOT_IN_GMAIL, log_path)
        if as_utc(date) > self.clock() - timedelta(days=min_age):
            return self._skip(off_id, item_id, SKIP_TOO_NEW.format(days=min_age), log_path)
        # 1. Only a copy here that is whole and unchanged lets anything change in Gmail.
        problem = await self._copy_problem(path, size, sha)
        if problem:
            return self._skip(off_id, item_id, SKIP_BAD_COPY.format(problem=problem), log_path)
        try:
            await adapter.trash(pid)  # 2.
        except MailNotFound:
            return self._skip(off_id, item_id, SKIP_GONE, log_path)
        except (MailAuthError, MailSetupProblem, MailUnreachable):
            raise
        except MailApiError as e:
            self._skip(off_id, item_id, SKIP_REFUSED.format(error=e), log_path)
            return str(e)
        return await self._check_trashed(adapter, off_id, item_id, msg_id, pid, log_path)

    async def _check_trashed(
        self,
        adapter,
        off_id: int,
        item_id: int,
        msg_id: int,
        pid: str,
        log_path,
        outside: str = SKIP_NOT_TRASHED,
    ) -> None:
        try:
            st = await adapter.state(pid)  # 3.
        except MailNotFound:
            return self._skip(off_id, item_id, SKIP_GONE, log_path)
        except (MailAuthError, MailSetupProblem, MailUnreachable):
            raise
        except MailApiError as e:
            return self._skip(off_id, item_id, SKIP_UNCHECKED.format(error=e), log_path)
        if st.where == GONE:
            return self._skip(off_id, item_id, SKIP_GONE, log_path)
        if st.where != IN_TRASH:
            return self._skip(off_id, item_id, outside, log_path)
        self._mark_offloaded(off_id, item_id, msg_id)
        job_log(log_path, f"offloaded {pid}")

    # ---- the job -------------------------------------------------------------------------
    def _fail_before_start(self, off_id: int, account_id: int, email: str, reason: str) -> None:
        with self.sf() as db:
            off = db.get(MailOffload, off_id)
            off.state, off.reason, off.finished_at = MailOffloadState.FAILED, reason, self.clock()
            db.commit()
            record_event(
                db,
                self.bus,
                kind="mail_offload",
                level="error",
                message=f"Mail offload for {email}: {reason}",
                account_id=account_id,
                data={"mail_offload_id": off_id},
            )
        self._changed(account_id, off_id)

    async def _run(self, account_id: int, args: dict, trigger: str) -> None:
        off_id = int(args["offload_id"])
        with self.sf() as db:
            off = db.get(MailOffload, off_id)
            acc = db.get(Account, account_id)
            if off is None or acc is None or off.state != MailOffloadState.WAITING:
                return
            email = acc.email
            # A backup that is listing the whole mailbox needs Gmail to hold still (Plan 2's
            # mark_seen_back_in_mail): wait for it, the minute tick asks again.
            phase = sync_state(db, account_id).phase
            if phase in (MailPhase.INITIAL, MailPhase.RECHECK):
                wait = BACKUP_CHECKING_MSG if phase == MailPhase.RECHECK else BACKUP_FIRST_MSG
                if off.reason != wait:
                    off.reason = wait
                    db.commit()
                    self._changed(account_id, off_id)
                return
            mail_state = service_state(db, account_id, ServiceKind.MAIL)
        if acc.status != AccountStatus.CONNECTED or mail_state == ServiceState.NEEDS_RECONNECT:
            return self._fail_before_start(off_id, account_id, email, OFFLOAD_RECONNECT_MSG)
        if mail_state != ServiceState.ACTIVE:
            return self._fail_before_start(off_id, account_id, email, OFFLOAD_MAIL_OFF_MSG)
        with self.sf() as db:
            off = db.get(MailOffload, off_id)
            off.state, off.reason = MailOffloadState.RUNNING, None
            db.commit()
        self._changed(account_id, off_id)
        job = None
        state, reason = MailOffloadState.DONE, None
        try:
            with self.sf() as db:
                batch = int(get_setting(db, "mail_batch_size"))  # type: ignore[arg-type]
                total = db.get(MailOffload, off_id).total
                job = start_mail_job(
                    db, self.layout, self.bus, account_id, MailJobKind.OFFLOAD, trigger, total
                )
            adapter = self.mail.adapter_for(acc)
            refused: list[str] = []  # Gmail's refusals in a row
            while True:
                with self.sf() as db:
                    items = list(
                        db.scalars(
                            select(MailOffloadItem.id)
                            .where(
                                MailOffloadItem.offload_id == off_id,
                                MailOffloadItem.result == MailItemResult.PENDING,
                            )
                            .order_by(MailOffloadItem.id)
                            .limit(batch)
                        )
                    )
                if not items:
                    break
                if await self.mail.stopped():  # before every batch (spec 3.4)
                    raise Paused(self.mail.stop_message() or MISSING_DISK_MSG)
                for item_id in items:
                    error = await self._offload_one(acc, adapter, off_id, item_id, job.log_path)
                    refused = [*refused, error] if error else []
                    if len(refused) >= REFUSED_IN_A_ROW:
                        raise _Refusing(OFFLOAD_REFUSED_MSG.format(n=len(refused), error=error))
                with self.sf() as db:
                    off = db.get(MailOffload, off_id)
                    update_mail_job(db, job.id, done=off.done, bytes_=off.bytes)
                self._changed(account_id, off_id)  # one "mail" event per batch
        except BudgetReached as e:
            state, reason = MailOffloadState.WAITING, str(e)  # goes on the next UTC day
        except RateLimited:
            state, reason = MailOffloadState.WAITING, OFFLOAD_SLOW_MSG  # the next minute
        except (Paused, _Refusing) as e:
            state, reason = MailOffloadState.FAILED, str(e)
        except MailAuthError as e:
            await self.mail._auth_lost(account_id, e)
            state, reason = MailOffloadState.FAILED, f"{e} {REST_STAYED}"
        except MailSetupProblem as e:
            state, reason = MailOffloadState.FAILED, f"{e} {REST_STAYED}"  # says what to fix
        except MailApiError as e:
            state, reason = MailOffloadState.FAILED, OFFLOAD_TROUBLE_MSG.format(error=e)
        except Exception as e:
            log.exception("mail offload %s failed", off_id)
            state, reason = MailOffloadState.FAILED, f"Something went wrong: {e}"
        try:
            self._finish(account_id, email, off_id, job, state, reason)
        except Exception:
            # Never leave it RUNNING. (The mail queue closes a job row left RUNNING.)
            log.exception("could not close mail offload %s", off_id)
            self._never_running(off_id)
        self._changed(account_id, off_id)
        self.mail.budget.flush()

    def _finish(self, account_id: int, email: str, off_id: int, job, state, reason) -> None:
        with self.sf() as db:
            off = db.get(MailOffload, off_id)
            if state == MailOffloadState.DONE and off.skipped:
                state = MailOffloadState.DONE_WITH_SKIPS
            off.state, off.reason = state, reason
            if state != MailOffloadState.WAITING:
                off.finished_at = self.clock()
            db.commit()
            text = summary(off)
            job_state = {
                MailOffloadState.DONE: JobState.SUCCESS,
                MailOffloadState.DONE_WITH_SKIPS: JobState.SUCCESS,
                MailOffloadState.WAITING: JobState.PAUSED,
            }.get(state, JobState.FAILED)
            message = f"{text}. {reason}" if reason else text
            if job is not None:
                finish_mail_job(
                    db, self.bus, job.id, job_state, message, done=off.done, bytes_=off.bytes
                )
            level = {JobState.SUCCESS: "info", JobState.PAUSED: "warning"}.get(job_state, "error")
            record_event(
                db,
                self.bus,
                kind="mail_offload",
                level=level,
                message=f"Mail offload for {email}: {message}",
                account_id=account_id,
                data={"mail_offload_id": off_id},
            )

    def _never_running(self, off_id: int) -> None:
        try:
            with self.sf() as db:
                off = db.get(MailOffload, off_id)
                if off is not None and off.state == MailOffloadState.RUNNING:
                    off.state = MailOffloadState.FAILED
                    off.reason = "Something went wrong while closing this offload. " + REST_STAYED
                    off.finished_at = self.clock()
                    db.commit()
        except Exception:
            log.exception("could not mark mail offload %s as failed", off_id)
