# backend/app/services/rules/worker.py
"""Sorting new inbox mail (AI rules spec §3): the keep-up step adds each new inbox mail to
rule_todo; this worker, outside the mail queue, decides each one and records the decision.
A model that can't be reached leaves the mail waiting, retried later; nothing is guessed."""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import and_, case, delete, select, update
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import Account, MailMessage, RuleAccount, RuleDecision, RuleJob, RuleTodo
from app.models.enums import DecisionHow, DecisionStatus, MailState, RuleJobState, RuleMode
from app.paths import Layout
from app.services.ai.client import AiError, AiPaused
from app.services.events.bus import EventBus
from app.services.rules.apply import WAIT_ERRORS, WAITING_REASONS, Applier
from app.services.rules.decisions import hints_for, remembered_senders
from app.services.rules.feedback import moved_back
from app.services.rules.mailview import CopyUnreadable, mail_for_model
from app.services.rules.match import Chooser, decide
from app.services.rules.store import rule_views

log = logging.getLogger(__name__)

GIVE_UP_AFTER = 10
SOMETHING_WRONG = "Something went wrong sorting this mail. CleanSync will try again."
GAVE_UP = (
    "Something went wrong sorting this mail 10 times, so CleanSync stopped trying. "
    "Run rules on it again to retry."
)


def retry_after(attempts: int) -> timedelta:
    return timedelta(minutes=min(2 ** max(attempts - 1, 0), 30))


def _account(db: Session, account_id: int) -> RuleAccount:
    row = db.get(RuleAccount, account_id)
    if row is None:
        row = RuleAccount(account_id=account_id, sorting_enabled=False)
        db.add(row)
        db.flush()
    return row


def set_sorting(db: Session, account_id: int, enabled: bool) -> RuleAccount:
    row = _account(db, account_id)
    row.sorting_enabled = enabled
    if not enabled:
        row.failing_since = None
        db.execute(
            delete(RuleTodo).where(RuleTodo.account_id == account_id, RuleTodo.job_id.is_(None))
        )
    return row


def enqueue_new(db: Session, account_id: int, row: MailMessage) -> None:
    acc = db.get(RuleAccount, account_id)
    if acc is None or not acc.sorting_enabled:
        return
    db.merge(RuleTodo(account_id=account_id, message_id=row.id, next_try_at=utcnow()))


def wake_waiting(db: Session, now) -> int:
    """Resume now (model limits spec §9): every to-do row waiting for later is due now."""
    return (
        db.execute(
            update(RuleTodo)
            .where(RuleTodo.next_try_at > now)
            .values(next_try_at=now)
            .execution_options(synchronize_session=False)
        ).rowcount
        or 0
    )


class SortingWorker:
    def __init__(
        self,
        *,
        session_factory,
        layout: Layout,
        chooser: Callable[[], Chooser | None],
        applier: Applier,
        bus: EventBus | None = None,
        clock=utcnow,
        idle_s: float = 15.0,
        batch: int = 20,
    ) -> None:
        self.sf = session_factory
        self.layout = layout
        self.chooser = chooser
        self.applier = applier
        self.bus = bus
        self.clock = clock
        self.idle_s = idle_s
        self.batch = batch
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None  # set by start()

    # ---- one step, for tests and the loop ------------------------------------------------
    # A new run of rules can take a mail over while it is being decided: start_preview stops
    # the old preview and queues the mail again under the new job, with the same key. So every
    # write that touches a to-do row names the job the mail was being decided for, and is the
    # first write of its transaction. SQLite then holds the database from that write until the
    # commit: a new run either got in before it (no row matches, so the old verdict is thrown
    # away and the new job's row is left for the next pass) or waits for the commit.
    @staticmethod
    def _mine(account_id: int, message_id: int, job_id: int | None) -> tuple:
        """Conditions for the mail's to-do row, as long as it still belongs to job_id."""
        return (
            RuleTodo.account_id == account_id,
            RuleTodo.message_id == message_id,
            RuleTodo.job_id.is_(None) if job_id is None else RuleTodo.job_id == job_id,
        )

    def _take(self, db: Session, account_id: int, message_id: int, job_id: int | None) -> bool:
        """Remove the mail's to-do row if it still belongs to job_id, and count it toward the
        job as decided (every removal goes here, as the first write). False: nothing done."""
        gone = db.execute(
            delete(RuleTodo)
            .where(*self._mine(account_id, message_id, job_id))
            .execution_options(synchronize_session=False)
        ).rowcount
        if gone != 1:
            return False
        if job_id is not None:
            db.execute(
                update(RuleJob)
                .where(RuleJob.id == job_id)
                .values(
                    decided=RuleJob.decided + 1,
                    state=case(
                        (
                            and_(
                                RuleJob.state == RuleJobState.PREVIEWING,
                                RuleJob.decided + 1 >= RuleJob.total,
                            ),
                            RuleJobState.PREVIEWED,
                        ),
                        else_=RuleJob.state,
                    ),
                )
                .execution_options(synchronize_session=False)
            )
        return True

    def _wait(
        self,
        db: Session,
        account_id: int,
        message_id: int,
        job_id: int | None,
        attempts: int,
        reason: str,
    ) -> bool:
        """Leave the mail waiting for a later try, if its row still belongs to job_id."""
        return (
            db.execute(
                update(RuleTodo)
                .where(*self._mine(account_id, message_id, job_id), RuleTodo.attempts == attempts)
                .values(
                    attempts=attempts + 1,
                    last_error=reason,
                    next_try_at=self.clock() + retry_after(attempts + 1),
                )
                .execution_options(synchronize_session=False)
            ).rowcount
            == 1
        )

    def _attempts(self, db: Session, account_id: int, message_id: int, job_id: int | None):
        return db.scalar(
            select(RuleTodo.attempts).where(*self._mine(account_id, message_id, job_id))
        )

    def _give_up(
        self, db: Session, account_id: int, message_id: int, job_id: int | None, reason: str
    ) -> bool:
        if not self._take(db, account_id, message_id, job_id):
            return False
        row = db.get(MailMessage, message_id)
        db.add(
            RuleDecision(
                account_id=account_id,
                message_id=message_id,
                how=DecisionHow.NONE,
                status=DecisionStatus.FAILED,
                reason=reason,
                job_id=job_id,
                labels_seen=list(row.labels or []) if row else [],
            )
        )
        return True

    def _failed_try(
        self, account_id: int, message_id: int, job_id: int | None, reason: str, gave_up: str
    ) -> tuple[int, bool]:
        """A try that went wrong: the mail waits for a later try, or is given up after
        GIVE_UP_AFTER tries. Returns (mails finished, anything changed)."""
        with self.sf() as db:
            attempts = self._attempts(db, account_id, message_id, job_id)
            if attempts is None:
                return 0, False
            if attempts + 1 >= GIVE_UP_AFTER:
                done = self._give_up(db, account_id, message_id, job_id, gave_up)
                changed = done
            else:
                done, changed = (
                    False,
                    self._wait(db, account_id, message_id, job_id, attempts, reason),
                )
            db.commit()
            return int(done), changed

    async def _retry_waiting(self, account_id: int) -> bool:
        """Apply decisions that were waiting on Gmail. True when any was tried."""
        with self.sf() as db:
            ids = list(
                db.scalars(
                    select(RuleDecision.id).where(
                        RuleDecision.account_id == account_id,
                        RuleDecision.status == DecisionStatus.SUGGESTED,
                        RuleDecision.reason.in_(WAITING_REASONS),
                    )
                )
            )
        tried = False
        for did in ids:
            try:
                await self.applier.apply(did)
                tried = True
            except WAIT_ERRORS:
                return tried
            except Exception:
                tried = True
                log.exception("applying decision %s failed", did)
        return tried

    async def run_once(self, account_id: int) -> int:
        changed = await self._retry_waiting(account_id)
        now = self.clock()
        with self.sf() as db:
            acc = db.get(Account, account_id)
            ra = db.get(RuleAccount, account_id)
            sorting_on = bool(ra and ra.sorting_enabled)
            ids = list(
                db.scalars(
                    select(RuleTodo.message_id)
                    .where(RuleTodo.account_id == account_id, RuleTodo.next_try_at <= now)
                    .order_by(RuleTodo.job_id.is_not(None), RuleTodo.added_at)
                    .limit(self.batch)
                )
            )
            email = acc.email if acc else ""
        chooser = self.chooser()
        handled = 0
        for message_id in ids:
            try:
                done, did_change, stop = await self._sort_one(
                    account_id, email, message_id, chooser, now, sorting_on
                )
            except Exception:  # reading the to-do row itself failed: it is tried again later
                log.exception("sorting mail %s failed", message_id)
                done, did_change, stop = 0, True, False
            handled += done
            changed = changed or did_change
            if stop:
                break
        if changed and self.bus is not None:
            self.bus.publish("rules", {"account_id": account_id})
        return handled

    async def _sort_one(
        self, account_id, email, message_id, chooser, now, sorting_on
    ) -> tuple[int, bool, bool]:
        """Decide one to-do mail. Returns (mails finished, anything changed, stop the batch).
        The job comes from the to-do row read here; every later write checks the row still
        belongs to it (see _mine)."""
        with self.sf() as db:
            todo = db.get(RuleTodo, (account_id, message_id))
            if todo is None:
                return 0, False, False
            job_id = todo.job_id
        try:
            return await self._decide_one(
                account_id, email, message_id, job_id, chooser, now, sorting_on
            )
        except Exception:
            log.exception("sorting mail %s failed", message_id)
            done, _ = self._failed_try(account_id, message_id, job_id, SOMETHING_WRONG, GAVE_UP)
            return done, True, False

    async def _decide_one(
        self, account_id, email, message_id, job_id, chooser, now, sorting_on
    ) -> tuple[int, bool, bool]:
        with self.sf() as db:
            row = db.get(MailMessage, message_id)
            if (
                (job_id is None and not sorting_on)
                or row is None
                or row.state != MailState.IN_MAIL
                or moved_back(db, message_id)
            ):
                if not self._take(db, account_id, message_id, job_id):
                    return 0, False, False
                db.commit()
                return 1, True, False
        # The mail file is read off the event loop, with no database session held open.
        try:
            mail = await asyncio.to_thread(mail_for_model, self.layout, email, row)
        except CopyUnreadable as e:
            reason = f"The copy here can't be read ({e})."
            done, changed = self._failed_try(account_id, message_id, job_id, reason, reason)
            return done, changed, False
        with self.sf() as db:
            rules = rule_views(db, account_id)
            remembered = remembered_senders(db, account_id)
            hints = hints_for(db, account_id, mail.sender)
            labels_seen = list(row.labels or [])
        try:
            verdict = await decide(rules, mail, remembered=remembered, chooser=chooser, hints=hints)
        except AiPaused as e:
            # Model limits spec §8: a paused model costs the mail nothing. It waits for the
            # pause to end and the batch goes on, since the next mail may need no model.
            until = e.pause.until or now + timedelta(hours=1)
            with self.sf() as db:
                db.execute(
                    update(RuleTodo)
                    .where(*self._mine(account_id, message_id, job_id))
                    .values(next_try_at=until)
                    .execution_options(synchronize_session=False)
                )
                db.commit()
            return 0, True, False
        except AiError as e:
            with self.sf() as db:
                attempts = self._attempts(db, account_id, message_id, job_id)
                if attempts is not None:
                    self._wait(db, account_id, message_id, job_id, attempts, str(e))
                ra = _account(db, account_id)
                ra.failing_since = ra.failing_since or now
                ra.last_error = str(e)
                db.commit()
            return 0, True, True  # the model is down: the rest would fail the same way
        with self.sf() as db:
            if not self._take(db, account_id, message_id, job_id):
                return 0, False, False  # a new run took the mail over, or sorting was turned off
            status = (
                DecisionStatus.NO_RULE
                if verdict.rule is None
                else DecisionStatus.PLANNED
                if job_id
                else DecisionStatus.SUGGESTED
            )
            d = RuleDecision(
                account_id=account_id,
                message_id=message_id,
                rule_id=verdict.rule.id if verdict.rule else None,
                rule_version=verdict.rule.version if verdict.rule else None,
                rule_name=verdict.rule.name if verdict.rule else "",
                how=verdict.how,
                why=verdict.why,
                model=verdict.model,
                took_ms=verdict.took_ms,
                raw_answer=verdict.raw,
                actions=list(verdict.rule.actions) if verdict.rule else [],
                labels_seen=labels_seen,
                status=status,
                job_id=job_id,
            )
            db.add(d)
            ra = _account(db, account_id)
            ra.failing_since, ra.last_error, ra.last_ok_at = None, None, self.clock()
            db.commit()
            did = d.id
        if verdict.rule is not None and not job_id and verdict.rule.mode == RuleMode.AUTOMATIC:
            try:
                await self.applier.apply(did)
            except WAIT_ERRORS:
                pass  # waits with its reason; retried at the start of the next run
        return 1, True, False

    # ---- the loop ------------------------------------------------------------------------
    def _accounts_with_work(self) -> list[int]:
        now = self.clock()
        with self.sf() as db:
            due = db.scalars(
                select(RuleTodo.account_id).where(RuleTodo.next_try_at <= now).distinct()
            )
            waiting = db.scalars(
                select(RuleDecision.account_id)
                .where(
                    RuleDecision.status == DecisionStatus.SUGGESTED,
                    RuleDecision.reason.in_(WAITING_REASONS),
                )
                .distinct()
            )
            return sorted({*due, *waiting})

    def poke(self) -> None:
        """Wake the loop. Safe from any thread: sync API endpoints call it from worker threads,
        where setting the Event directly wouldn't wake a loop that is asleep."""
        loop = self._event_loop
        try:
            on_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_loop = False
        if loop is not None and not on_loop and not loop.is_closed():
            loop.call_soon_threadsafe(self._wake.set)
        else:
            self._wake.set()

    async def _loop(self) -> None:
        while True:
            self._wake.clear()
            did = 0
            try:
                for account_id in self._accounts_with_work():
                    try:
                        did += await self.run_once(account_id)
                    except Exception:
                        log.exception("sorting mail for account %s failed", account_id)
            except Exception:
                log.exception("looking for mail to sort failed")
            if did == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), self.idle_s)

    def start(self) -> None:
        if self._task is None:
            self._event_loop = asyncio.get_running_loop()
            self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
