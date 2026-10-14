"""Unsubscribe jobs (0.5.2 §4.1): one sender at a time, in the background, one job per
mailbox. Kinds: unsubscribe (the steps in steps.py), auto_archive (a Gmail filter) and keep.

A restart carries on with the senders that have no outcome yet: recover() puts RUNNING jobs
back to WAITING, and the next mail tick starts them."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import Account, MailMessage, UnsubscribeJob, UnsubscribeJobItem, UnsubscribeSender
from app.models.enums import UnsubscribeJobKind, UnsubscribeJobState
from app.services.events.bus import EventBus, record_event
from app.services.google.client import RateLimited
from app.services.google.quota import BudgetReached
from app.services.mail.adapter import MailAuthError, MailUnreachable
from app.services.mail.reader import MailFileMissing, read_eml_of
from app.services.mail.service import MailError
from app.services.unsubscribe.senders import KEEP, newest_with_link, set_status
from app.services.unsubscribe.steps import (
    AUTO_ARCHIVED,
    DONE_KEPT,
    KEPT,
    NEEDS_YOU,
    NO_LINK_MSG,
    NOT_CONFIRMED,
    UNSUBSCRIBED,
    FilterGate,
    Ladder,
    StepResult,
)

log = logging.getLogger(__name__)

ALREADY_RUNNING_MSG = "An unsubscribe is already running for this mailbox."
STOPPED_MSG = "Stopped by you."
PICK_ONE_MSG = "Pick at least one sender."
TOO_MANY_MSG = "Pick at most 5,000 senders at a time."
MAX_ADDRESSES = 5000
SETUP_FAILED_MSG = "CleanSync couldn't open this mailbox. Try again later."

WAITING = UnsubscribeJobState.WAITING.value
RUNNING = UnsubscribeJobState.RUNNING.value
OPEN_STATES = (WAITING, RUNNING)


def _senders(n: int) -> str:
    return f"{n:,} sender" if n == 1 else f"{n:,} senders"


def summary(kind: str, counts: Counter) -> str:
    """The Activity line: "Unsubscribed from 41 senders, 6 need you"."""
    if kind == UnsubscribeJobKind.AUTO_ARCHIVE:
        text = f"Auto-archived {_senders(counts[AUTO_ARCHIVED])}"
    elif kind == UnsubscribeJobKind.KEEP:
        text = f"Kept {_senders(counts[KEPT])}"
    else:
        text = f"Unsubscribed from {_senders(counts[UNSUBSCRIBED])}"
        if counts[AUTO_ARCHIVED]:
            text += f", {counts[AUTO_ARCHIVED]:,} auto-archived"
    need = counts[NOT_CONFIRMED] + counts[NEEDS_YOU]
    if need:
        text += f", {need:,} {'needs' if need == 1 else 'need'} you"
    return text


def job_out(db: Session, job: UnsubscribeJob) -> dict:
    return {
        "id": job.id,
        "account_id": job.account_id,
        "kind": job.kind,
        "state": job.state,
        "total": job.total,
        "done": job.done,
        "stop_asked": bool(job.stop_asked),
        "reason": job.reason,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }


def items_out(db: Session, job_id: int) -> list[dict]:
    rows = db.scalars(
        select(UnsubscribeJobItem)
        .where(UnsubscribeJobItem.job_id == job_id)
        .order_by(UnsubscribeJobItem.id)
    )
    return [
        {
            "address": r.address,
            "outcome": r.outcome,
            "method": r.method,
            "note": r.note,
            "link": r.link,
        }
        for r in rows
    ]


def running_job(db: Session, account_id: int) -> UnsubscribeJob | None:
    return db.scalar(
        select(UnsubscribeJob)
        .where(UnsubscribeJob.account_id == account_id, UnsubscribeJob.state.in_(OPEN_STATES))
        .order_by(UnsubscribeJob.id)
        .limit(1)
    )


class _JobEnds(Exception):
    """Gmail login trouble, Gmail out of reach, Google's limits, or no way to open the
    mailbox: the job fails; senders not reached keep no outcome."""


class UnsubscribeJobs:
    def __init__(
        self,
        *,
        sf,
        bus: EventBus,
        mail,
        web,
        ai,
        connections,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.sf = sf
        self.bus = bus
        self.mail = mail
        self.web = web
        self.ai = ai
        self.connections = connections
        self.clock = clock
        self._tasks: dict[int, asyncio.Task] = {}

    running_job = staticmethod(running_job)
    job_out = staticmethod(job_out)

    def _changed(self, account_id: int, job_id: int) -> None:
        self.bus.publish("unsubscribe", {"account_id": account_id, "job_id": job_id})

    # ---- start, stop, recover, tick -------------------------------------------------------
    async def start(self, account_id: int, kind: str, addresses: list[str]) -> int:
        kind = UnsubscribeJobKind(kind).value
        clean = list(dict.fromkeys(a.strip().lower() for a in addresses if a and a.strip()))
        if not clean:
            raise MailError(PICK_ONE_MSG, 400)
        if len(clean) > MAX_ADDRESSES:
            raise MailError(TOO_MANY_MSG, 400)
        await self.mail.ready(account_id)  # the disk, the login, Mail set up
        with self.sf() as db:
            if running_job(db, account_id) is not None:
                raise MailError(ALREADY_RUNNING_MSG, 409)
            job = UnsubscribeJob(
                account_id=account_id,
                kind=kind,
                state=WAITING,
                total=len(clean),
                created_at=self.clock(),
            )
            db.add(job)
            db.flush()
            db.add_all(UnsubscribeJobItem(job_id=job.id, address=a) for a in clean)
            db.commit()
            job_id = job.id
        self._spawn(job_id)
        self._changed(account_id, job_id)
        return job_id

    def stop(self, job_id: int) -> None:
        """A waiting job ends now; a running one after its current sender."""
        with self.sf() as db:
            job = db.get(UnsubscribeJob, job_id)
            if job is None:
                raise LookupError("That unsubscribe job doesn't exist.")
            if job.state == WAITING and job_id not in self._tasks:
                job.state = UnsubscribeJobState.STOPPED.value
                job.reason, job.finished_at = STOPPED_MSG, self.clock()
            elif job.state in OPEN_STATES:
                job.stop_asked = True
            db.commit()
            account_id = job.account_id
        self._changed(account_id, job_id)

    def recover(self) -> None:
        """At startup: jobs a restart cut short wait to go on."""
        with self.sf() as db:
            for job in db.scalars(select(UnsubscribeJob).where(UnsubscribeJob.state == RUNNING)):
                job.state = WAITING
            db.commit()

    def tick(self) -> None:
        """A mail tick hook: start each waiting job that has no task."""
        with self.sf() as db:
            ids = list(db.scalars(select(UnsubscribeJob.id).where(UnsubscribeJob.state == WAITING)))
        for job_id in ids:
            self._spawn(job_id)

    def _spawn(self, job_id: int) -> None:
        if job_id in self._tasks:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (a sync caller): the next tick starts it
        task = loop.create_task(self._run(job_id))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t, j=job_id: self._tasks.pop(j, None))

    async def wait(self, job_id: int) -> None:
        task = self._tasks.get(job_id)
        if task is not None:
            await asyncio.shield(task)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---- one job ------------------------------------------------------------------------
    def _read_eml(self, email: str, mail_id: int) -> bytes | None:
        with self.sf() as db:
            row = db.get(MailMessage, mail_id)
        if row is None or not row.file_path:
            return None
        try:
            return read_eml_of(self.mail.layout, email, row)
        except (MailFileMissing, OSError, ValueError):
            return None

    async def _run(self, job_id: int) -> None:
        with self.sf() as db:
            job = db.get(UnsubscribeJob, job_id)
            if job is None or job.state not in OPEN_STATES:
                return
            acc = db.get(Account, job.account_id)
            if acc is None:
                return
            job.state = RUNNING
            db.commit()
            account_id, kind, email = job.account_id, job.kind, acc.email
        self._changed(account_id, job_id)
        state, reason = UnsubscribeJobState.DONE.value, None
        try:
            try:
                adapter = self.mail.adapter_for(acc)
            except (MailAuthError, MailUnreachable) as e:
                raise _JobEnds(str(e)) from None
            except Exception:
                log.exception("unsubscribe job %s: no mail adapter", job_id)
                raise _JobEnds(SETUP_FAILED_MSG) from None
            filters = FilterGate(adapter=adapter, sf=self.sf, bus=self.bus, account_id=account_id)
            ladder = Ladder(
                self.web,
                self.ai,
                self.connections,
                read_eml=lambda mid: self._read_eml(email, mid),
            )
            while True:
                with self.sf() as db:
                    job = db.get(UnsubscribeJob, job_id)
                    item = db.scalar(
                        select(UnsubscribeJobItem)
                        .where(
                            UnsubscribeJobItem.job_id == job_id,
                            UnsubscribeJobItem.outcome.is_(None),
                        )
                        .order_by(UnsubscribeJobItem.id)
                        .limit(1)
                    )
                    stop_asked = bool(job.stop_asked)
                if item is None:
                    break
                if stop_asked:
                    state, reason = UnsubscribeJobState.STOPPED.value, STOPPED_MSG
                    break
                if await self.mail.stopped():
                    state = UnsubscribeJobState.STOPPED.value
                    reason = self.mail.stop_message() or STOPPED_MSG
                    break
                res = await self._one(kind, acc, item.address, ladder, filters)
                self._save(account_id, job_id, item.id, item.address, res)
                log.info("unsubscribe job %s: %s -> %s", job_id, item.address, res.outcome)
                self._changed(account_id, job_id)
        except _JobEnds as e:
            state, reason = UnsubscribeJobState.FAILED.value, str(e)
        except asyncio.CancelledError:
            raise  # shutdown: the job stays RUNNING, and recover() picks it up next start
        except Exception as e:
            log.exception("unsubscribe job %s failed", job_id)
            state, reason = (
                UnsubscribeJobState.FAILED.value,
                f"Something went wrong: {type(e).__name__}",
            )
        self._finish(account_id, job_id, kind, state, reason)

    async def _one(
        self, kind: str, acc: Account, address: str, ladder: Ladder, filters: FilterGate
    ) -> StepResult:
        try:
            if kind == UnsubscribeJobKind.AUTO_ARCHIVE:
                return await filters.auto_archive(address)
            if kind == UnsubscribeJobKind.KEEP:
                return await self._keep(acc.id, address, filters)
            with self.sf() as db:
                row = newest_with_link(db, acc.id, acc.email, address)
            if row is None:
                return StepResult(NOT_CONFIRMED, None, NO_LINK_MSG, None)
            return await ladder.unsubscribe(row, filters=filters)
        except (MailAuthError, MailUnreachable, BudgetReached, RateLimited) as e:
            raise _JobEnds(str(e)) from None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("unsubscribe of %s failed", address)
            return StepResult(
                NOT_CONFIRMED, None, f"Something went wrong: {type(e).__name__}", None
            )

    async def _keep(self, account_id: int, address: str, filters: FilterGate) -> StepResult:
        with self.sf() as db:
            filter_id = db.scalar(
                select(UnsubscribeSender.filter_id).where(
                    UnsubscribeSender.account_id == account_id,
                    UnsubscribeSender.address == address,
                )
            )
        if filter_id:
            return await filters.remove(filter_id)
        return StepResult(KEPT, None, DONE_KEPT, None)  # no filter: no permission needed

    def _save(self, account_id: int, job_id: int, item_id: int, address: str, res: StepResult):
        now = self.clock()
        # Unsubscribed, Auto-archived and Kept are saved; the others leave the status alone.
        filter_id = {UNSUBSCRIBED: KEEP, AUTO_ARCHIVED: res.filter_id, KEPT: None}
        with self.sf() as db:
            if res.outcome in filter_id:
                set_status(
                    db,
                    account_id,
                    address,
                    res.outcome,
                    method=res.method,
                    note=res.note,
                    filter_id=filter_id[res.outcome],
                    now=now,
                    commit=False,
                )
            item = db.get(UnsubscribeJobItem, item_id)
            item.outcome, item.method, item.note, item.link = (
                res.outcome,
                res.method,
                res.note,
                res.link,
            )
            job = db.get(UnsubscribeJob, job_id)
            job.done += 1
            db.commit()

    def _finish(
        self, account_id: int, job_id: int, kind: str, state: str, reason: str | None
    ) -> None:
        """Never raises: a job left RUNNING would refuse every later start until a restart."""
        try:
            with self.sf() as db:
                job = db.get(UnsubscribeJob, job_id)
                job.state, job.reason, job.finished_at = state, reason, self.clock()
                db.commit()
        except Exception:
            log.exception("unsubscribe job %s: couldn't save its end", job_id)
            return
        try:
            self._record_end(account_id, job_id, kind, state, reason)
        except Exception:
            log.exception("unsubscribe job %s: couldn't add the Activity line", job_id)
        self._changed(account_id, job_id)

    def _record_end(
        self, account_id: int, job_id: int, kind: str, state: str, reason: str | None
    ) -> None:
        with self.sf() as db:
            counts = Counter(
                db.scalars(
                    select(UnsubscribeJobItem.outcome).where(
                        UnsubscribeJobItem.job_id == job_id,
                        UnsubscribeJobItem.outcome.is_not(None),
                    )
                )
            )
            message = summary(kind, counts)
            if reason:
                message += f". {reason}"
            record_event(
                db,
                self.bus,
                kind="unsubscribe_job_finished",
                level="warning" if state == UnsubscribeJobState.FAILED else "info",
                message=message,
                account_id=account_id,
                data={"unsubscribe_job_id": job_id},
            )
