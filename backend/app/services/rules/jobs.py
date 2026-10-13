"""Running rules by hand (running rules spec): a run decides the mail it covers with its own
rules, using only CleanSync's copy (nothing changes in Gmail). Then the owner approves from the
match list and the picked changes are made. Approving counts as the approval, whatever each
rule's mode is."""

import asyncio
import contextlib
import logging
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import MailMessage, Rule, RuleDecision, RuleJob, RuleTodo
from app.models.enums import DecisionStatus, MailState, RuleJobState
from app.services.mail.offload_plan import Pick, describe_pick
from app.services.mail.search import GMAIL_VIEWS, SearchError, mail_conditions
from app.services.rules.apply import WAIT_ERRORS, Applier
from app.services.rules.worker import SortingWorker

log = logging.getLogger(__name__)
MAX_JOB = 20000
EXAMPLES = 5
WAIT_SECONDS = 60
CHANGED_MSG = "It changed since the preview, so it was left as it is."
APPLY_FAILED_MSG = "Something went wrong applying this change."
STOPPED_MSG = "The run was stopped."
RUN_CHANGED_MSG = "A run of rules changed this mail."
RULE_CHANGED_MSG = "Its rule changed since the preview, so it was left as it is."
OPEN_STATES = (RuleJobState.PREVIEWING, RuleJobState.PREVIEWED, RuleJobState.RUNNING)
NO_RULES_MSG = "Pick at least one rule."
ONE_RULE_MSG = "Run on the Inbox takes one rule."
RULE_GONE_MSG = "That rule no longer exists."
RULE_OFF_MSG = "Turn the rule on to run it."
NO_SCOPE_MSG = "Search first, or pick some mail."
VIEW_MSG = "Rules run on mail in Gmail's views only."
NO_MAIL_MSG = "No mail in Gmail matches this search."
EMPTY_INBOX_MSG = "No mail in the Inbox to run this rule on."
# Gmail's views a run can cover (spec §4). Suggested is a list of suggestions, not of mail.
RUN_VIEWS = GMAIL_VIEWS - {"suggested"}


class RuleJobProblem(ValueError):
    pass


@dataclass
class RunScope:
    """What a run covers (running rules spec §8): the Inbox (a rule card's Run), a search or a
    pick-all as the view, label and search, or mails or threads picked by hand. Picked threads
    count only their mails in `view` (and `label`)."""

    inbox: bool = False
    view: str = "all"
    label: str | None = None
    q: str = ""
    pick_all: bool = False
    message_ids: list[int] | None = None
    thread_ids: list[str] | None = None


def _check_rules(db: Session, account_id: int, rule_ids: list[int]) -> None:
    found = {
        r.id: r
        for r in db.scalars(
            select(Rule).where(Rule.account_id == account_id, Rule.id.in_(rule_ids))
        )
    }
    for rid in rule_ids:
        if rid not in found:
            raise RuleJobProblem(RULE_GONE_MSG)
        if not found[rid].enabled:
            raise RuleJobProblem(RULE_OFF_MSG)


def _scope_conditions(db: Session, account_id: int, scope: RunScope, now) -> list:
    """Conditions for the mail a run covers, beyond "this mailbox, in Gmail"."""
    if scope.inbox:
        return mail_conditions(db, account_id, view="inbox", now=now)
    if scope.message_ids:
        return [MailMessage.id.in_(scope.message_ids[: MAX_JOB + 1])]
    if scope.view not in RUN_VIEWS:
        raise RuleJobProblem(VIEW_MSG)
    if scope.thread_ids:
        return [
            MailMessage.thread_id.in_(scope.thread_ids),
            *mail_conditions(db, account_id, view=scope.view, label=scope.label, now=now),
        ]
    if scope.pick_all or scope.q.strip():
        return mail_conditions(
            db, account_id, view=scope.view, label=scope.label, q=scope.q, now=now
        )
    raise RuleJobProblem(NO_SCOPE_MSG)


def _search_of(scope: RunScope) -> str:
    """The search a run went through: "" for the Inbox and for a pick by hand."""
    if scope.inbox or scope.message_ids or scope.thread_ids:
        return ""
    return scope.q.strip()


def _describe(db: Session, account_id: int, scope: RunScope, n: int) -> str:
    """What the run covers, in words: "Inbox", "Inbox · from:shop", "3 picked mails"."""
    if scope.inbox:
        return describe_pick(db, account_id, Pick(view="inbox"), n)
    pick = Pick(
        q=_search_of(scope),
        message_ids=scope.message_ids,
        thread_ids=scope.thread_ids,
        view=scope.view,
        label=scope.label,
    )
    return describe_pick(db, account_id, pick, n) or "All mail"


@dataclass
class JobPreview:
    job_id: int
    state: str
    total: int
    decided: int
    planned: int
    per_rule: list[dict]
    no_rule: int
    applied: int
    skipped: int
    failed: int
    reason: str | None
    rule_ids: list[int]
    view: str
    q: str
    filter_text: str


class RuleJobs:
    def __init__(self, session_factory, applier: Applier, worker: SortingWorker, clock=utcnow):
        self.sf = session_factory
        self.applier = applier
        self.worker = worker
        self.clock = clock
        self._tasks: dict[int, asyncio.Task] = {}

    def _job(self, db, account_id: int, job_id: int) -> RuleJob:
        job = db.get(RuleJob, job_id)
        if job is None or job.account_id != account_id:
            raise RuleJobProblem("That run isn't here any more.")
        return job

    def start_preview(self, account_id: int, *, rule_ids: list[int], scope: RunScope) -> int:
        """Queue every mail the scope covers, to be decided with `rule_ids` only."""
        now = self.clock()
        rule_ids = list(dict.fromkeys(rule_ids))
        if not rule_ids:
            raise RuleJobProblem(NO_RULES_MSG)
        if scope.inbox and len(rule_ids) != 1:
            raise RuleJobProblem(ONE_RULE_MSG)
        with self.sf() as db:
            _check_rules(db, account_id, rule_ids)
            try:
                conds = _scope_conditions(db, account_id, scope, now)
            except SearchError as e:
                raise RuleJobProblem(str(e)) from None
            base = select(MailMessage.id).where(
                MailMessage.account_id == account_id,
                MailMessage.state == MailState.IN_MAIL,
                *conds,
            )
            open_jobs = list(
                db.scalars(
                    select(RuleJob).where(
                        RuleJob.account_id == account_id, RuleJob.state.in_(OPEN_STATES)
                    )
                )
            )
            if any(j.state == RuleJobState.RUNNING for j in open_jobs):
                raise RuleJobProblem(
                    "Another run of rules for this mailbox isn't finished. Run or stop it first."
                )
            # A preview nobody went on with (its dialog was closed) gives way to the new one.
            for j in open_jobs:
                self._stop_in(db, j)
            found = list(db.scalars(base.order_by(MailMessage.id).limit(MAX_JOB + 1)))
            if len(found) > MAX_JOB:
                raise RuleJobProblem(
                    f"That's more than {MAX_JOB:,} mails. Please narrow the search."
                )
            moved = set(
                db.scalars(
                    select(RuleDecision.message_id)
                    .where(
                        RuleDecision.account_id == account_id,
                        RuleDecision.moved_back_at.is_not(None),
                    )
                    .distinct()
                )
            )
            queued = set(
                db.scalars(select(RuleTodo.message_id).where(RuleTodo.account_id == account_id))
            )
            ids = [i for i in found if i not in moved and i not in queued]
            if not ids:
                raise RuleJobProblem(EMPTY_INBOX_MSG if scope.inbox else NO_MAIL_MSG)
            job = RuleJob(
                account_id=account_id,
                filter_text=_describe(db, account_id, scope, len(ids)),
                total=len(ids),
                rule_ids=rule_ids,
                view="inbox" if scope.inbox else scope.view,
                q=_search_of(scope),
            )
            db.add(job)
            db.flush()
            for mid in ids:
                db.add(
                    RuleTodo(
                        account_id=account_id,
                        message_id=mid,
                        job_id=job.id,
                        next_try_at=now,
                        added_at=now,
                    )
                )
            db.commit()
            jid = job.id
        self.worker.poke()
        return jid

    def preview(self, account_id: int, job_id: int) -> JobPreview:
        with self.sf() as db:
            job = self._job(db, account_id, job_id)
            failed = db.scalar(
                select(func.count()).where(
                    RuleDecision.job_id == job_id, RuleDecision.status == DecisionStatus.FAILED
                )
            )
            counts = dict(
                db.execute(
                    select(RuleDecision.status, func.count())
                    .where(RuleDecision.job_id == job_id)
                    .group_by(RuleDecision.status)
                ).all()
            )
            groups: dict[tuple, dict] = defaultdict(lambda: {"count": 0, "examples": []})
            rows = db.execute(
                select(
                    RuleDecision.rule_id,
                    RuleDecision.rule_name,
                    RuleDecision.actions,
                    RuleDecision.why,
                    MailMessage.subject,
                    MailMessage.from_addr,
                )
                .join(MailMessage, MailMessage.id == RuleDecision.message_id)
                .where(RuleDecision.job_id == job_id, RuleDecision.rule_id.is_not(None))
                .order_by(RuleDecision.id)
            ).all()
            for rule_id, name, actions, why, subject, sender in rows:
                g = groups[(rule_id, name)]
                g["count"] += 1
                g["actions"] = actions
                if len(g["examples"]) < EXAMPLES:
                    g["examples"].append({"subject": subject, "sender": sender, "why": why})
            ordered = sorted(groups.items(), key=lambda x: -x[1]["count"])
            return JobPreview(
                job_id=job.id,
                state=job.state,
                total=job.total,
                decided=job.decided,
                planned=counts.get(DecisionStatus.PLANNED, 0),
                per_rule=[{"rule": n, "rule_id": rid, **g} for (rid, n), g in ordered],
                no_rule=counts.get(DecisionStatus.NO_RULE, 0),
                applied=job.applied,
                skipped=job.skipped,
                failed=failed or 0,
                reason=job.reason,
                rule_ids=list(job.rule_ids or []),
                view=job.view,
                q=job.q,
                filter_text=job.filter_text,
            )

    def run(self, account_id: int, job_id: int, confirm: int) -> None:
        with self.sf() as db:
            job = self._job(db, account_id, job_id)
            if job.state != RuleJobState.PREVIEWED:
                raise RuleJobProblem("Wait until the preview is finished.")
            planned = db.scalar(
                select(func.count()).where(
                    RuleDecision.job_id == job_id, RuleDecision.status == DecisionStatus.PLANNED
                )
            )
            if confirm != planned:
                raise RuleJobProblem(
                    f"The number you typed doesn't match the {planned:,} mails to change."
                )
            job.state = RuleJobState.RUNNING
            db.commit()
        try:
            self._start(job_id)
        except Exception:
            with self.sf() as db:
                db.get(RuleJob, job_id).state = RuleJobState.PREVIEWED
                db.commit()
            raise

    def _start(self, job_id: int) -> None:
        live = self._tasks.get(job_id)
        if live is not None and not live.done():
            return
        self._tasks[job_id] = asyncio.ensure_future(self._run(job_id))

    def _still_same(self, db, d: RuleDecision) -> str | None:
        row = db.get(MailMessage, d.message_id)
        if (
            row is None
            or row.state != MailState.IN_MAIL
            or sorted(row.labels or []) != sorted(d.labels_seen or [])
        ):
            return CHANGED_MSG
        rule = db.get(Rule, d.rule_id) if d.rule_id else None
        if rule is None or rule.version != d.rule_version or not rule.enabled:
            return RULE_CHANGED_MSG
        return None

    async def _run(self, job_id: int) -> None:
        try:
            while True:
                with self.sf() as db:
                    job = db.get(RuleJob, job_id)
                    if job is None or job.state != RuleJobState.RUNNING:
                        return
                    d = db.scalar(
                        select(RuleDecision)
                        .where(
                            RuleDecision.job_id == job_id,
                            RuleDecision.status == DecisionStatus.PLANNED,
                        )
                        .order_by(RuleDecision.id)
                    )
                    if d is None:
                        job.state, job.finished_at = RuleJobState.DONE, self.clock()
                        db.commit()
                        return
                    why_not = self._still_same(db, d)
                    if why_not:
                        d.status, d.reason = DecisionStatus.SKIPPED, why_not
                        job.skipped += 1
                        db.commit()
                        continue
                    did = d.id
                try:
                    status = await self.applier.apply(did)
                except asyncio.CancelledError:
                    raise
                except WAIT_ERRORS as e:
                    with self.sf() as db:
                        job = db.get(RuleJob, job_id)
                        waiting = db.get(RuleDecision, did)
                        job.reason = (waiting.reason if waiting else None) or str(e)
                        db.commit()
                    await asyncio.sleep(WAIT_SECONDS)
                    continue
                except Exception:
                    log.exception("applying decision %s of rule job %s failed", did, job_id)
                    with self.sf() as db:
                        db.get(RuleDecision, did).status = DecisionStatus.FAILED
                        db.get(RuleDecision, did).reason = APPLY_FAILED_MSG
                        db.get(RuleJob, job_id).failed += 1
                        db.commit()
                    continue
                with self.sf() as db:
                    job = db.get(RuleJob, job_id)
                    job.reason = None
                    if status == DecisionStatus.APPLIED:
                        job.applied += 1
                        self._withdraw_suggestions(db, did)
                    else:
                        job.failed += 1
                    db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("rule job %s failed", job_id)
            with self.sf() as db:
                job = db.get(RuleJob, job_id)
                if job is not None:
                    job.state = RuleJobState.FAILED
                    job.reason = f"Something went wrong: {e}"
                    job.finished_at = self.clock()
                    db.commit()

    def _withdraw_suggestions(self, db, decision_id: int) -> None:
        """The run changed this mail: older suggestions for it no longer fit."""
        d = db.get(RuleDecision, decision_id)
        db.execute(
            update(RuleDecision)
            .where(
                RuleDecision.account_id == d.account_id,
                RuleDecision.message_id == d.message_id,
                RuleDecision.id != decision_id,
                RuleDecision.status == DecisionStatus.SUGGESTED,
            )
            .values(status=DecisionStatus.WITHDRAWN, reason=RUN_CHANGED_MSG)
            .execution_options(synchronize_session=False)
        )

    def _stop_in(self, db, job: RuleJob) -> None:
        """Drop the job's to-do rows, skip its planned changes and mark it stopped (no commit)."""
        db.execute(delete(RuleTodo).where(RuleTodo.job_id == job.id))
        left = db.execute(
            update(RuleDecision)
            .where(RuleDecision.job_id == job.id, RuleDecision.status == DecisionStatus.PLANNED)
            .values(status=DecisionStatus.SKIPPED, reason=STOPPED_MSG)
            .execution_options(synchronize_session=False)
        ).rowcount
        job.skipped += left
        if job.state in OPEN_STATES:
            job.state, job.finished_at = RuleJobState.STOPPED, self.clock()

    def open_job(self, account_id: int) -> int | None:
        """The mailbox's unfinished run of rules, if there is one."""
        with self.sf() as db:
            return db.scalar(
                select(RuleJob.id)
                .where(RuleJob.account_id == account_id, RuleJob.state.in_(OPEN_STATES))
                .order_by(RuleJob.id.desc())
                .limit(1)
            )

    def stop(self, account_id: int, job_id: int) -> None:
        with self.sf() as db:
            self._stop_in(db, self._job(db, account_id, job_id))
            db.commit()
        task = self._tasks.pop(job_id, None)
        if task is not None:
            task.cancel()

    async def wait(self, job_id: int) -> None:
        task = self._tasks.get(job_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def resume(self) -> None:
        with self.sf() as db:
            ids = list(db.scalars(select(RuleJob.id).where(RuleJob.state == RuleJobState.RUNNING)))
        for jid in ids:
            self._start(jid)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
