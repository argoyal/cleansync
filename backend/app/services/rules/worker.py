# backend/app/services/rules/worker.py
"""Sorting new inbox mail (AI rules spec §3): the keep-up step adds each new inbox mail to
rule_todo; this worker, outside the mail queue, decides each one and records the decision.
A model that can't be reached leaves the mail waiting, retried later; nothing is guessed."""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import Account, MailMessage, RuleAccount, RuleDecision, RuleJob, RuleTodo
from app.models.enums import DecisionStatus, MailState, RuleJobState, RuleMode
from app.paths import Layout
from app.services.ai.client import AiError
from app.services.events.bus import EventBus
from app.services.rules.apply import WAIT_ERRORS, WAITING_REASONS, Applier
from app.services.rules.decisions import hints_for, remembered_senders
from app.services.rules.feedback import moved_back
from app.services.rules.mailview import CopyUnreadable, mail_for_model
from app.services.rules.match import Chooser, decide
from app.services.rules.store import rule_views

log = logging.getLogger(__name__)


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
    return row


def enqueue_new(db: Session, account_id: int, row: MailMessage) -> None:
    acc = db.get(RuleAccount, account_id)
    if acc is None or not acc.sorting_enabled:
        return
    db.merge(RuleTodo(account_id=account_id, message_id=row.id, next_try_at=utcnow()))


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

    # ---- one step, for tests and the loop ------------------------------------------------
    def _wait(self, db: Session, todo: RuleTodo, reason: str) -> None:
        todo.attempts += 1
        todo.last_error = reason
        todo.next_try_at = self.clock() + retry_after(todo.attempts)

    async def _retry_waiting(self, account_id: int) -> None:
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
        for did in ids:
            try:
                await self.applier.apply(did)
            except WAIT_ERRORS:
                return

    async def run_once(self, account_id: int) -> int:
        await self._retry_waiting(account_id)
        now = self.clock()
        with self.sf() as db:
            acc = db.get(Account, account_id)
            todos = list(
                db.scalars(
                    select(RuleTodo)
                    .where(RuleTodo.account_id == account_id, RuleTodo.next_try_at <= now)
                    .order_by(RuleTodo.added_at)
                    .limit(self.batch)
                )
            )
            keys = [(t.message_id, t.job_id) for t in todos]
            email = acc.email if acc else ""
        chooser = self.chooser()
        handled = 0
        for message_id, job_id in keys:
            with self.sf() as db:
                todo = db.get(RuleTodo, (account_id, message_id))
                row = db.get(MailMessage, message_id)
                if todo is None:
                    continue
                if row is None or row.state != MailState.IN_MAIL or moved_back(db, message_id):
                    db.delete(todo)
                    db.commit()
                    handled += 1
                    continue
                try:
                    mail = mail_for_model(self.layout, email, row)
                except CopyUnreadable as e:
                    self._wait(db, todo, f"The copy here can't be read ({e}).")
                    db.commit()
                    continue
                rules = rule_views(db, account_id)
                remembered = remembered_senders(db, account_id)
                hints = hints_for(db, account_id, mail.sender)
                labels_seen = list(row.labels or [])
            try:
                verdict = await decide(
                    rules, mail, remembered=remembered, chooser=chooser, hints=hints
                )
            except AiError as e:
                with self.sf() as db:
                    todo = db.get(RuleTodo, (account_id, message_id))
                    self._wait(db, todo, str(e))
                    ra = _account(db, account_id)
                    ra.failing_since = ra.failing_since or now
                    ra.last_error = str(e)
                    db.commit()
                break  # the model is down: the rest of this batch would fail the same way
            with self.sf() as db:
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
                db.delete(db.get(RuleTodo, (account_id, message_id)))
                ra = _account(db, account_id)
                ra.failing_since, ra.last_error, ra.last_ok_at = None, None, self.clock()
                if job_id:
                    job = db.get(RuleJob, job_id)
                    if job is not None:
                        job.decided += 1
                        if job.decided >= job.total and job.state == RuleJobState.PREVIEWING:
                            job.state = RuleJobState.PREVIEWED
                db.commit()
                did = d.id
            handled += 1
            if verdict.rule is not None and not job_id and verdict.rule.mode == RuleMode.AUTOMATIC:
                try:
                    await self.applier.apply(did)
                except WAIT_ERRORS:
                    pass  # waits with its reason; retried at the start of the next run
        if handled and self.bus is not None:
            self.bus.publish("rules", {"account_id": account_id})
        return handled

    # ---- the loop ------------------------------------------------------------------------
    def _accounts_with_work(self) -> list[int]:
        now = self.clock()
        with self.sf() as db:
            return list(
                db.scalars(
                    select(RuleTodo.account_id).where(RuleTodo.next_try_at <= now).distinct()
                )
            )

    def poke(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            did = 0
            for account_id in self._accounts_with_work():
                try:
                    did += await self.run_once(account_id)
                except Exception:
                    log.exception("sorting mail for account %s failed", account_id)
            if did == 0:
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), self.idle_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
