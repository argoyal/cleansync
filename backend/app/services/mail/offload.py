"""Mail offload and restore (spec 5): out of Gmail by filter or by pick, into Gmail's trash
(checked before and after), and back into Gmail without ever making a second copy.
Runs as mail queue actions, so never beside a backup of the account."""

import asyncio
import logging
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import as_utc, utcnow
from app.models import (
    Account,
    MailJob,
    MailLabel,
    MailMessage,
    MailOffload,
    MailOffloadItem,
)
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
    SKIP_LABELS,
    MailApiError,
    MailAuthError,
    MailNotFound,
    MailSetupProblem,
    MailUnreachable,
    ProviderState,
)
from app.services.mail.backup import Paused
from app.services.mail.gmail import search_message_id
from app.services.mail.index import delete_search_rows, mark_restored, sync_state
from app.services.mail.jobs import finish_mail_job, job_log, start_mail_job, update_mail_job
from app.services.mail.offload_plan import (
    GMAIL_TRASH_DAYS,
    OffloadPreview,
    Pick,
    chunked,
    plan_offload,
    rows_where_in,
)
from app.services.mail.search import SearchError, mail_conditions
from app.services.mail.service import MailError, MailService
from app.services.mail.store import check_eml, check_eml_bytes, eml_path
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
# Restore (spec 5.5).
NOTHING_TO_RESTORE_MSG = "Nothing here needs restoring: these mails are in Gmail."
RESTORE_SLOW_MSG = (
    "Gmail asked CleanSync to slow down. The restore goes on by itself in a minute or so."
)
RESTORE_STAYED = "What wasn't put back yet stays here; restore it again."
RESTORE_RECONNECT_MSG = f"Reconnect this Google account. {RESTORE_STAYED}"
RESTORE_MAIL_OFF_MSG = "Mail backup isn't on for this account any more."
RESTORE_REFUSED_MSG = "Gmail refused {n} mails in a row ({error}). " + RESTORE_STAYED
RESTORE_TROUBLE_MSG = "CleanSync couldn't reach Gmail ({error}). " + RESTORE_STAYED
# Labels an import always keeps; when Gmail refuses an import, it goes again with only these.
BASIC_LABELS = ("INBOX", "UNREAD", "STARRED", "IMPORTANT")
UNTRASHED, IMPORTED, ALREADY, IN_SPAM, FAILED = "untrashed", "imported", "already", "spam", "failed"
BATCH_CHECK = 100  # a restore looks for the disk and tells the pages every this many mails


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


def restorable_labels(db: Session, account_id: int, labels: list[str]) -> list[str]:
    """The labels a mail goes back with: never Trash, Spam, Draft or Chat; user labels only
    while they still exist in Gmail."""
    users = set(
        db.scalars(
            select(MailLabel.provider_label_id).where(
                MailLabel.account_id == account_id, MailLabel.type == "user"
            )
        )
    )
    return [
        lab
        for lab in labels
        if lab not in SKIP_LABELS
        and (lab in users or lab in BASIC_LABELS or lab == "SENT" or lab.startswith("CATEGORY_"))
    ]


def restore_targets(
    db: Session, account_id: int, pick: Pick, offload_id: int | None, now: datetime
) -> list[int]:
    """The offloaded and Deleted-in-Gmail mails of a pick (or of one offload): the only ones
    a restore touches. Mail already in Gmail is never part of it."""
    restorable = (MailState.OFFLOADED, MailState.DELETED)
    if offload_id is None and (pick.message_ids or pick.thread_ids):
        # A picked list can be longer than SQLite takes in one query: asked in chunks.
        column = MailMessage.id if pick.message_ids else MailMessage.thread_id
        rows = rows_where_in(db, account_id, column, pick.message_ids or pick.thread_ids or [])
        found = {r.id: r for r in rows if r.state in restorable}
        return [r.id for r in sorted(found.values(), key=lambda r: (as_utc(r.date), r.id))]
    q = select(MailMessage.id).where(
        MailMessage.account_id == account_id,
        MailMessage.state.in_(restorable),
    )
    if offload_id is not None:
        q = q.where(MailMessage.offload_id == offload_id)
    elif (pick.q or "").strip():
        q = q.where(
            *mail_conditions(db, account_id, view="kept", q=(pick.q or "").strip(), now=now)
        )
    else:
        return []
    return list(db.scalars(q.order_by(MailMessage.date, MailMessage.id)))


def _mails(n: int) -> str:
    return f"{n:,} {'mail' if n == 1 else 'mails'}"


def restore_summary(c: Counter) -> str:
    back = c[UNTRASHED] + c[IMPORTED]
    text = f"Put {_mails(back)} back in Gmail"
    detail = []
    if c[UNTRASHED]:
        detail.append(f"{c[UNTRASHED]:,} out of Gmail's trash")
    if c[IMPORTED]:
        detail.append(f"{c[IMPORTED]:,} imported")
    if detail:
        text += f" ({', '.join(detail)})"
    if c[ALREADY]:
        text += f"; {c[ALREADY]:,} {'was' if c[ALREADY] == 1 else 'were'} already there"
    if c[IN_SPAM]:
        one = c[IN_SPAM] == 1
        text += f"; {c[IN_SPAM]:,} {'is' if one else 'are'} in Gmail's Spam, so "
        text += f"{'it was' if one else 'they were'} left there"
    if c[FAILED]:
        text += f"; {c[FAILED]:,} couldn't be put back (see the log)"
    return text


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
        mail.actions["restore"] = self._restore
        mail.tick_hooks.append(self.resume_waiting)
        # Restores paused by the budget or a rate limit: account -> the mails still to do and
        # the paused jobs they came from. Kept in memory: at startup recover_mail_restores
        # closes those jobs, and the mails, still offloaded, can be restored again.
        self._restores_waiting: dict[int, dict[str, list[int]]] = {}

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
        waiting, self._restores_waiting = self._restores_waiting, {}
        for account_id, w in waiting.items():
            args = {"message_ids": w["ids"], "after_jobs": w["jobs"]}
            self.mail.queue.request(account_id, "restore", args)

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

    # ---- restore (spec 5.5) --------------------------------------------------------------
    async def request_restore(
        self, account_id: int, pick: Pick, offload_id: int | None = None
    ) -> tuple[str, int]:
        """Refused, changing nothing, like an offload: the disk, the login, Mail set up, and
        a backup that is not still listing the whole mailbox."""
        await self.mail.ready(account_id)
        with self.sf() as db:
            if sync_state(db, account_id).phase in (MailPhase.INITIAL, MailPhase.RECHECK):
                raise MailError(BACKUP_CHECKING_MSG)
        try:
            with self.sf() as db:
                ids = restore_targets(db, account_id, pick, offload_id, self.clock())
        except SearchError as e:
            raise MailError(str(e), 422) from e
        if not ids:
            raise MailError(NOTHING_TO_RESTORE_MSG)
        queued = self.mail.queue.request(
            account_id, "restore", {"message_ids": ids}, trigger="MANUAL"
        )
        return queued, len(ids)

    async def _read_copy(self, acc: Account, info: dict, log_path) -> bytes | None:
        """The copy here, read once and checked (size, SHA-256, reads as a mail): exactly
        these bytes are sent. None when it can't be used; a lost disk stops the run."""
        path = eml_path(self.layout, acc.email, info["file_path"])
        try:
            raw = await asyncio.to_thread(path.read_bytes)
            problem = check_eml_bytes(raw, info["size"], info["sha"])
        except FileNotFoundError:
            raw, problem = b"", "the file is missing"
        except OSError:
            raw, problem = b"", "the file can't be read"
        if problem:
            if await self.mail.stopped() or not await asyncio.to_thread(
                self.layout.mail_root.is_dir
            ):
                raise Paused(self.mail.stop_message() or MISSING_DISK_MSG)
            job_log(log_path, f"{info['pid']}: the copy here didn't check out ({problem})")
            return None
        return raw

    async def _import_copy(
        self, acc: Account, adapter, msg_id: int, info: dict, log_path
    ) -> str | None:
        """Gmail no longer has the mail: send the copy here. Returns the new Gmail id, or None
        when the copy can't be used. The row is marked first: should Gmail take the import
        and its answer get lost, the backup and the next restore know that new mail is this
        one (see adopt_restored_copy and _copy_in_gmail)."""
        raw = await self._read_copy(acc, info, log_path)
        if raw is None:
            return None
        with self.sf() as db:
            db.get(MailMessage, msg_id).import_started_at = self.clock()
            db.commit()
        try:
            return await adapter.import_raw(raw, info["labels"], info["date"])
        except (MailAuthError, MailSetupProblem, MailUnreachable, MailNotFound):
            raise
        except MailApiError as e:
            if e.status != 400:
                raise
            # Gmail refused the labels (say, one it won't take): go again with the basic ones.
            job_log(log_path, f"{info['pid']}: Gmail refused the labels ({e}); trying without")
            basic = [x for x in info["labels"] if x in BASIC_LABELS]
            return await adapter.import_raw(raw, basic, info["date"])

    async def _copy_in_gmail(self, acc: Account, adapter, msg_id: int, info: dict) -> str | None:
        """Before any import: is this mail in Gmail under another id? An earlier import whose
        answer was lost (or the owner's own upload) is found by its Message-ID and adopted, so
        a restore asked again never imports it twice. Ids other mails here already have are
        those mails, not this one."""
        if search_message_id(info["message_id"]) is None:
            return None  # no Message-ID to look for: imported as it is
        ids = await adapter.find_by_message_id(info["message_id"])
        if not ids:
            return None
        with self.sf() as db:
            owners = list(
                db.scalars(
                    select(MailMessage).where(
                        MailMessage.account_id == acc.id,
                        MailMessage.provider_id.in_(ids),
                        MailMessage.id != msg_id,
                    )
                )
            )
            mine = [r for r in owners if self._own_import(r, info)]
            taken = {r.provider_id for r in owners if r not in mine}
            # Never lose a mail file: only a byte-for-byte second copy (same SHA-256 as the
            # kept one) is removed; any other stays in mail/, unlisted.
            files = [
                (eml_path(self.layout, acc.email, r.file_path), r.sha256 == info["sha"])
                for r in mine
            ]
            if mine:
                gone = [r.id for r in mine]
                for r in mine:
                    db.delete(r)
                db.commit()
                delete_search_rows(db, gone)
        kept = eml_path(self.layout, acc.email, info["file_path"])
        kept_ok = bool(files) and not await asyncio.to_thread(
            check_eml, kept, info["size"], info["sha"]
        )
        for path, same in files:
            # The same mail saved a second time by a check: the copy here stays the one, but
            # only while it checks out; else the second copy may be the only good one.
            if not (same and kept_ok):
                log.warning("merged a second row for one mail; kept an unlisted copy at %s", path)
                continue
            try:
                await asyncio.to_thread(path.unlink, missing_ok=True)
            except OSError:
                log.warning("could not remove the second copy %s", path, exc_info=True)
        return next((x for x in ids if x not in taken), None)

    @staticmethod
    def _own_import(other: MailMessage, info: dict) -> bool:
        """A row a check made for this restore's own import whose answer got lost: same
        Message-ID, first seen after the import started, and nothing done with it since."""
        started = info["import_started_at"]
        return (
            started is not None
            and other.message_id == info["message_id"]
            and other.state == MailState.IN_MAIL
            and other.offload_id is None
            and as_utc(other.first_seen_at) >= as_utc(started)
        )

    async def _untrash(self, adapter, pid: str) -> ProviderState:
        try:
            await adapter.untrash(pid)
            return await adapter.state(pid)
        except MailNotFound:
            return ProviderState(GONE, (), None)  # Gmail emptied it from its trash just now

    async def _restore_one(self, acc: Account, adapter, msg_id: int, log_path) -> str:
        """Spec 5.5. In Gmail's trash: untrash (its labels come back). Gone from Gmail: look
        for it by its Message-ID first, and import the copy here only when Gmail has none;
        keep Gmail's new id. In Gmail already: nothing to do. A restore never makes a second
        copy, also when it is asked again after an answer from Gmail got lost."""
        with self.sf() as db:
            row = db.get(MailMessage, msg_id)
            if row is None or row.account_id != acc.id:
                return FAILED
            if row.state == MailState.IN_MAIL:
                return ALREADY
            info = {
                "pid": row.provider_id,
                "message_id": row.message_id,
                "labels": restorable_labels(db, acc.id, list(row.labels or [])),
                "date": row.date,
                "file_path": row.file_path,
                "size": row.file_size,
                "sha": row.sha256,
                "import_started_at": row.import_started_at,
            }
        pid, new_id, outcome = info["pid"], None, None
        st = await adapter.state(pid)
        for _ in range(2):  # at most: the mail itself, then a copy found by its Message-ID
            if st.where == IN_TRASH:
                st = await self._untrash(adapter, pid)
                if st.where == IN_TRASH:
                    job_log(log_path, f"{pid}: Gmail didn't take it out of its trash")
                    return FAILED
                if st.where != GONE:
                    outcome = UNTRASHED
                    break
            if st.where != GONE:
                break
            found = await self._copy_in_gmail(acc, adapter, msg_id, info)
            if found is None:
                break
            job_log(log_path, f"{info['pid']}: found in Gmail as {found}; no import needed")
            pid = new_id = found
            st = await adapter.state(pid)
        if outcome is None and st.where == GONE and new_id is not None:
            # Gmail lists a copy but won't hand it over: never import beside it.
            job_log(log_path, f"{info['pid']}: Gmail gave mixed answers about {new_id}")
            return FAILED
        if outcome is None:
            if st.where == GONE:
                # Gmail has no copy at all: only now is the copy here sent.
                new_id = await self._import_copy(acc, adapter, msg_id, info, log_path)
                if new_id is None:
                    return FAILED
                outcome = IMPORTED
            elif "SPAM" in st.labels:
                job_log(log_path, f"{pid}: it is in Gmail's Spam, so it was left there")
                outcome = IN_SPAM  # stays Deleted in Gmail (spec 5.5)
            else:
                outcome = ALREADY  # the owner put it back in Gmail (or an earlier restore did)
        with self.sf() as db:
            row = db.get(MailMessage, msg_id)
            if row is None:  # deleted forever while Gmail was answering
                job_log(log_path, f"{pid}: the mail was deleted here meanwhile; not restored")
                return FAILED
            if new_id is not None:
                row.provider_id = new_id  # the file stays where it is (row.file_path)
            row.import_started_at = None  # the outcome is known
            if outcome != IN_SPAM:
                mark_restored(db, row)
            db.commit()
        if outcome != IN_SPAM:
            job_log(log_path, f"{pid}: {outcome}" + (f" as {new_id}" if new_id else ""))
        return outcome

    def _restore_refusal(self, account_id: int) -> str | None:
        """Checked again when the job runs: things may have changed since it was asked for."""
        with self.sf() as db:
            acc = db.get(Account, account_id)
            mail_state = service_state(db, account_id, ServiceKind.MAIL)
            phase = sync_state(db, account_id).phase
        if acc.status != AccountStatus.CONNECTED or mail_state == ServiceState.NEEDS_RECONNECT:
            return RESTORE_RECONNECT_MSG
        if mail_state != ServiceState.ACTIVE:
            return RESTORE_MAIL_OFF_MSG
        if phase in (MailPhase.INITIAL, MailPhase.RECHECK):
            return BACKUP_CHECKING_MSG
        return None

    async def _restore(self, account_id: int, args: dict, trigger: str) -> None:
        ids = [int(x) for x in args.get("message_ids") or []]
        with self.sf() as db:
            acc = db.get(Account, account_id)
            if acc is None or not ids:
                return
            job = start_mail_job(
                db, self.layout, self.bus, account_id, MailJobKind.RESTORE, trigger, len(ids)
            )
        self._close_paused(args.get("after_jobs") or [], job.id)
        counts: Counter[str] = Counter()
        state, problem = JobState.SUCCESS, self._restore_refusal(account_id)
        if problem:
            state = JobState.FAILED
        else:
            try:
                await self._restore_all(acc, ids, job, counts)
            except BudgetReached as e:
                state, problem = JobState.PAUSED, str(e)  # goes on the next UTC day
            except RateLimited:
                state, problem = JobState.PAUSED, RESTORE_SLOW_MSG  # the next minute
            except (Paused, _Refusing) as e:
                state, problem = JobState.FAILED, str(e)
            except MailAuthError as e:
                await self.mail._auth_lost(account_id, e)
                state, problem = JobState.FAILED, f"{e} {RESTORE_STAYED}"
            except MailSetupProblem as e:
                state, problem = JobState.FAILED, f"{e} {RESTORE_STAYED}"  # says what to fix
            except MailApiError as e:
                state, problem = JobState.FAILED, RESTORE_TROUBLE_MSG.format(error=e)
            except Exception as e:
                log.exception("mail restore for account %s failed", account_id)
                state, problem = JobState.FAILED, f"Something went wrong: {e}"
        if state == JobState.SUCCESS and counts[FAILED]:
            state = JobState.FAILED
        if state == JobState.PAUSED:
            done = set(self._restore_done(account_id, ids))
            if rest := [i for i in ids if i not in done]:
                self._wait_for_restore(account_id, rest, job.id)
        text = restore_summary(counts)
        message = f"{text}. {problem}" if problem else text
        with self.sf() as db:
            finish_mail_job(
                db, self.bus, job.id, state, message, done=counts[UNTRASHED] + counts[IMPORTED]
            )
            level = {JobState.SUCCESS: "info", JobState.PAUSED: "warning"}.get(state, "error")
            record_event(
                db,
                self.bus,
                kind="mail_restore",
                level=level,
                message=f"Mail restore for {acc.email}: {message}",
                account_id=account_id,
                data={"mail_job_id": job.id},
            )
        self._changed(account_id)
        self.mail.budget.flush()

    def _wait_for_restore(self, account_id: int, ids: list[int], job_id: int) -> None:
        """Asked again by the minute tick. A second paused restore of the account joins the
        first one's list: neither is dropped, and no mail is in it twice."""
        w = self._restores_waiting.setdefault(account_id, {"ids": [], "jobs": []})
        have = set(w["ids"])
        w["ids"] += [i for i in ids if i not in have]
        w["jobs"].append(job_id)

    def _close_paused(self, job_ids: list[int], now_job: int) -> None:
        """The paused jobs a resumed restore goes on from: Activity no longer shows them as
        waiting."""
        if not job_ids:
            return
        with self.sf() as db:
            for jid in job_ids:
                old = db.get(MailJob, int(jid))
                if old is not None and old.state == JobState.PAUSED:
                    finish_mail_job(
                        db,
                        self.bus,
                        old.id,
                        JobState.INTERRUPTED,
                        f"{old.message} It went on as restore job {now_job}.",
                    )

    def _restore_done(self, account_id: int, ids: list[int]) -> list[int]:
        """Of these, the mails no restore has to touch any more (back in Gmail, or gone)."""
        open_: set[int] = set()
        with self.sf() as db:
            for part in chunked(ids):  # SQLite takes few values in one query
                open_ |= set(
                    db.scalars(
                        select(MailMessage.id).where(
                            MailMessage.account_id == account_id,
                            MailMessage.id.in_(part),
                            MailMessage.state.in_([MailState.OFFLOADED, MailState.DELETED]),
                        )
                    )
                )
        return [i for i in ids if i not in open_]

    async def _restore_all(self, acc: Account, ids: list[int], job, counts: Counter) -> None:
        adapter = self.mail.adapter_for(acc)
        refused: list[str] = []
        for i, msg_id in enumerate(ids):
            if i % BATCH_CHECK == 0:
                if i:
                    with self.sf() as db:
                        update_mail_job(
                            db, job.id, done=counts[UNTRASHED] + counts[IMPORTED], bytes_=0
                        )
                    self._changed(acc.id)  # one "mail" event per batch
                if await self.mail.stopped():  # before every batch (spec 3.4)
                    raise Paused(self.mail.stop_message() or MISSING_DISK_MSG)
            try:
                outcome = await self._restore_one(acc, adapter, msg_id, job.log_path)
                refused = []
            except (MailAuthError, MailSetupProblem, MailUnreachable):
                raise
            except MailApiError as e:  # Gmail refused this one mail (or lost it): the rest go on
                job_log(job.log_path, f"mail {msg_id}: Gmail refused it ({e})")
                outcome = FAILED
                refused.append(str(e))
                if len(refused) >= REFUSED_IN_A_ROW:
                    counts[outcome] += 1
                    raise _Refusing(RESTORE_REFUSED_MSG.format(n=len(refused), error=e)) from e
            counts[outcome] += 1


RESTARTED_RESTORE_MSG = "CleanSync restarted before this restore finished. Restore it again."


def recover_mail_restores(db: Session) -> int:
    """At startup: a restore that was paused (its list of mails left lives only in memory)
    or cut short can't go on by itself. It ends as FAILED; its mails are safe and still
    show as offloaded, to be restored again."""
    jobs = list(
        db.scalars(
            select(MailJob).where(
                MailJob.kind == MailJobKind.RESTORE,
                MailJob.state.in_([JobState.PAUSED, JobState.RUNNING]),
            )
        )
    )
    for job in jobs:
        job.state = JobState.FAILED
        job.message = (
            f"{job.message} {RESTARTED_RESTORE_MSG}" if job.message else RESTARTED_RESTORE_MSG
        )
        job.completed_at = utcnow()
    db.commit()
    return len(jobs)
