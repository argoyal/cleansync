# backend/app/api/rules.py
"""Rules, decisions, sorting and rule jobs for one mailbox (AI rules spec §5, §6, §8)."""

import asyncio
from dataclasses import asdict
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.mail_reader import account_or_404
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import as_utc, utcnow
from app.models import MailMessage, Rule, RuleAccount, RuleDecision, RuleSender, RuleTodo
from app.models.enums import AiTaskName, DecisionStatus, RuleJobState
from app.schemas.ai import PauseOut
from app.schemas.rules import (
    AccountBody,
    CopyBody,
    CorrectBody,
    DecisionOut,
    DraftOut,
    IdsBody,
    JobPreviewOut,
    JobRunBody,
    JobStartBody,
    MailIdsBody,
    NamesBody,
    RuleBody,
    RuleOut,
    SenderOut,
    SentenceBody,
    SortingBody,
    SortingOut,
    TryBody,
    TryOut,
    YamlBody,
)
from app.services.ai.client import AiError, AiPaused
from app.services.google.client import RateLimited
from app.services.google.quota import BudgetReached
from app.services.mail.adapter import MailAuthError, MailUnreachable
from app.services.mail.archive import ArchiveProblem
from app.services.mail.search import SearchError, mail_conditions
from app.services.rules import drafting, store
from app.services.rules.apply import WAIT_ERRORS
from app.services.rules.decisions import (
    REMEMBER_AFTER,
    DecisionProblem,
    hints_for,
    remembered_senders,
)
from app.services.rules.jobs import RuleJobProblem
from app.services.rules.mailview import CopyUnreadable, mail_for_model
from app.services.rules.match import Conditions, RuleView
from app.services.rules.worker import set_sorting

router = APIRouter()
WARN_AFTER = timedelta(hours=1)


def _problem(e: Exception) -> HTTPException:
    if isinstance(e, store.RuleNotFound):
        return HTTPException(404, str(e))
    if isinstance(e, AiPaused):
        return HTTPException(503, str(e))
    if isinstance(e, AiError):
        return HTTPException(502, str(e))
    return HTTPException(400, str(e))


WAIT_MSG = "Gmail can't be changed right now ({why}). Try again later."


def _wait(e: Exception) -> HTTPException:
    if isinstance(e, BudgetReached):
        why = "Google's daily limit is reached"
    elif isinstance(e, RateLimited):
        why = "Google asked us to slow down"
    elif isinstance(e, MailAuthError):
        why = "the mailbox needs to be reconnected"
    elif isinstance(e, MailUnreachable):
        why = "Gmail can't be reached"
    else:
        why = "it is busy"
    return HTTPException(503, WAIT_MSG.format(why=why))


def _changed(request: Request, account_id: int) -> None:
    request.app.state.bus.publish("rules", {"account_id": account_id})


PROBLEMS = (
    store.RuleProblem,
    store.RuleNotFound,
    DecisionProblem,
    RuleJobProblem,
    ArchiveProblem,
    AiError,
)


def _rule_in(f) -> store.RuleIn:
    return store.RuleIn(
        name=f.name,
        description=f.description,
        conditions=f.conditions,
        combine=f.combine,
        actions=f.actions,
        mode=f.mode,
        enabled=f.enabled,
    )


def _rule_out(db: Session, rule: Rule) -> RuleOut:
    since = utcnow() - timedelta(days=7)
    decided = (
        db.scalar(
            select(func.count()).where(
                RuleDecision.rule_id == rule.id, RuleDecision.created_at >= since
            )
        )
        or 0
    )
    senders = [
        SenderOut(
            id=s.id,
            sender=s.sender,
            approvals=s.approvals,
            remembered=s.approvals >= REMEMBER_AFTER,
        )
        for s in db.scalars(
            select(RuleSender).where(RuleSender.rule_id == rule.id).order_by(RuleSender.sender)
        )
    ]
    return RuleOut(
        id=rule.id,
        name=rule.name,
        description=rule.description,
        conditions=rule.conditions,
        combine=rule.combine,
        actions=rule.actions,
        mode=rule.mode,
        enabled=rule.enabled,
        position=rule.position,
        version=rule.version,
        decided_7d=decided,
        senders=senders,
    )


# ---- rules ---------------------------------------------------------------------------------
@router.get("/api/rules", response_model=list[RuleOut])
def list_rules(
    account_id: int, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)
):
    account_or_404(db, account_id)
    rules = db.scalars(
        select(Rule).where(Rule.account_id == account_id).order_by(Rule.position, Rule.id)
    )
    return [_rule_out(db, r) for r in rules]


@router.post("/api/rules", response_model=RuleOut, status_code=201)
def create_rule(
    body: RuleBody, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)
):
    account_or_404(db, body.account_id)
    try:
        return _rule_out(
            db, store.create_rule(db, body.account_id, _rule_in(body), ctx.user.username)
        )
    except PROBLEMS as e:
        raise _problem(e) from e


def _sorting_pause(request: Request) -> PauseOut | None:
    found = request.app.state.ai.pause_for_task(AiTaskName.SORTING)
    if found is None:
        return None
    cid, name, p = found
    return PauseOut(
        kind=p.kind,
        until=p.until,
        reason=p.reason,
        detail=p.detail,
        since=p.since,
        connection_id=cid,
        connection_name=name,
    )


def _preview_out(request: Request, preview) -> JobPreviewOut:
    paused = _sorting_pause(request) if preview.state == RuleJobState.PREVIEWING else None
    return JobPreviewOut(**asdict(preview), paused=paused)


# Declared before /api/rules/{rule_id} on purpose: otherwise PUT /api/rules/sorting is read as a rule id.
# ---- sorting status ----------------------------------------------------------------------------
def _sorting_out(db: Session, request: Request, account_id: int) -> SortingOut:
    ra = db.get(RuleAccount, account_id)
    now = utcnow()
    today = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    count = lambda *w: db.scalar(select(func.count()).where(*w)) or 0  # noqa: E731
    failing = as_utc(ra.failing_since) if ra else None
    return SortingOut(
        enabled=bool(ra and ra.sorting_enabled),
        waiting=count(RuleTodo.account_id == account_id, RuleTodo.job_id.is_(None)),
        sorted_today=count(
            RuleDecision.account_id == account_id,
            RuleDecision.created_at >= today,
            RuleDecision.job_id.is_(None),
        ),
        suggested=count(
            RuleDecision.account_id == account_id, RuleDecision.status == DecisionStatus.SUGGESTED
        ),
        failing_since=failing,
        last_error=ra.last_error if ra else None,
        warning=bool(failing and now - failing > WARN_AFTER),
        model_set=request.app.state.ai.target(AiTaskName.SORTING) is not None,
        paused=_sorting_pause(request),
    )


@router.get("/api/rules/sorting", response_model=SortingOut)
def sorting(
    account_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, account_id)
    return _sorting_out(db, request, account_id)


@router.put("/api/rules/sorting", response_model=SortingOut)
def set_sorting_on(
    body: SortingBody,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, body.account_id)
    set_sorting(db, body.account_id, body.enabled)
    db.commit()
    return _sorting_out(db, request, body.account_id)


@router.put("/api/rules/{rule_id}", response_model=RuleOut)
def update_rule(
    rule_id: int,
    body: RuleBody,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, body.account_id)
    try:
        return _rule_out(
            db, store.update_rule(db, body.account_id, rule_id, _rule_in(body), ctx.user.username)
        )
    except PROBLEMS as e:
        raise _problem(e) from e


@router.delete("/api/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    account_id: int,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, account_id)
    try:
        store.delete_rule(db, account_id, rule_id)
    except PROBLEMS as e:
        raise _problem(e) from e
    return Response(status_code=204)


@router.post("/api/rules/reorder", status_code=204)
def reorder(body: IdsBody, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)):
    account_or_404(db, body.account_id)
    try:
        store.reorder(db, body.account_id, body.ids)
    except PROBLEMS as e:
        raise _problem(e) from e
    return Response(status_code=204)


@router.delete("/api/rules/{rule_id}/senders/{sender_id}", status_code=204)
def forget_sender(
    rule_id: int,
    sender_id: int,
    account_id: int,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, account_id)
    s = db.get(RuleSender, sender_id)
    if s is None or s.rule_id != rule_id or s.account_id != account_id:
        raise HTTPException(404, "That sender isn't remembered here.")
    db.delete(s)
    db.commit()
    return Response(status_code=204)


@router.get("/api/rules/starters")
def starters(ctx: AuthContext = Depends(require_user)):
    return [
        {"name": n, "description": r.description, "actions": r.actions}
        for n, r in store.STARTERS.items()
    ]


@router.post("/api/rules/starters", response_model=list[RuleOut])
def add_starters(
    body: NamesBody, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)
):
    account_or_404(db, body.account_id)
    return [
        _rule_out(db, r)
        for r in store.add_starters(db, body.account_id, body.names, ctx.user.username)
    ]


@router.get("/api/rules/export")
def export(
    account_id: int, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)
):
    acc = account_or_404(db, account_id)
    text = store.export_yaml(db, account_id)
    name = f"cleansync-rules-{acc.email.replace('@', '-at-')}.yaml"
    return Response(
        text,
        media_type="text/yaml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/api/rules/import", response_model=list[RuleOut])
def import_rules(
    body: YamlBody, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)
):
    account_or_404(db, body.account_id)
    try:
        return [
            _rule_out(db, r)
            for r in store.import_yaml(db, body.account_id, body.yaml, ctx.user.username)
        ]
    except PROBLEMS as e:
        raise _problem(e) from e


@router.post("/api/rules/copy")
def copy(body: CopyBody, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)):
    account_or_404(db, body.account_id)
    account_or_404(db, body.to_account_id)
    try:
        n = store.copy_rules(db, body.account_id, body.to_account_id, ctx.user.username)
    except PROBLEMS as e:
        raise _problem(e) from e
    return {"copied": n}


# ---- drafting and Try it -----------------------------------------------------------------------
def _writer(request: Request):
    return request.app.state.ai.target(AiTaskName.WRITING_RULES)


def _read_mails(session_factory, layout, account_id: int, email: str, ids: list[int]):
    """Runs in a worker thread, with its own database session: it reads mail files."""
    with session_factory() as db:
        rows = list(
            db.scalars(
                select(MailMessage).where(
                    MailMessage.account_id == account_id, MailMessage.id.in_(ids)
                )
            )
        )
    out = []
    for r in rows:
        try:
            out.append(mail_for_model(layout, email, r))
        except CopyUnreadable:
            continue  # a broken copy is simply left out of drafts and tries
    return out


async def _mails(request: Request, acc, ids: list[int]):
    state = request.app.state
    return await asyncio.to_thread(
        _read_mails, state.session_factory, state.layout, acc.id, acc.email, ids
    )


@router.post("/api/rules/draft", response_model=DraftOut)
async def draft(
    body: SentenceBody,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, body.account_id)
    try:
        d = await drafting.sentence_to_rule(
            request.app.state.ai_client, _writer(request), body.sentence
        )
    except AiError as e:
        raise _problem(e) from e
    return DraftOut(**asdict(d))


@router.post("/api/rules/draft-from-mail", response_model=DraftOut)
async def draft_from_mail(
    body: MailIdsBody,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    acc = account_or_404(db, body.account_id)
    mails = await _mails(request, acc, body.message_ids)
    if not mails:
        raise HTTPException(400, "None of those mails can be read here.")
    try:
        d = await drafting.rule_from_mails(request.app.state.ai_client, _writer(request), mails)
    except AiError as e:
        raise _problem(e) from e
    return DraftOut(**asdict(d))


@router.post("/api/rules/try", response_model=list[TryOut])
async def try_rule(
    body: TryBody,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    acc = account_or_404(db, body.account_id)
    if body.message_ids:
        ids = body.message_ids
    else:
        try:
            # An empty search tries the rule on the inbox, as the page says.
            look_in = "all" if body.q.strip() else "inbox"
            conds = mail_conditions(db, acc.id, view=look_in, q=body.q, now=utcnow())
        except SearchError as e:
            raise HTTPException(422, str(e)) from e
        ids = list(
            db.scalars(
                select(MailMessage.id)
                .where(*conds)
                .order_by(MailMessage.date.desc())
                .limit(drafting.TRY_LIMIT)
            )
        )
    f = body.rule
    view = RuleView(
        body.rule_id or -1,
        0,
        f.name or "This rule",
        f.description,
        Conditions.from_json(f.conditions),
        f.combine,
        tuple(f.actions),
        f.mode,
    )
    existing = store.rule_views(db, acc.id)
    remembered = {s: r for s, r in remembered_senders(db, acc.id).items() if r != view.id}
    mails = await _mails(request, acc, ids)
    try:
        results = await drafting.try_rule(
            view,
            existing,
            mails,
            request.app.state.sorting_chooser(),
            remembered,
            lambda s: hints_for(db, acc.id, s),
        )
    except AiError as e:
        raise _problem(e) from e
    return [TryOut(**asdict(r)) for r in results]


# ---- decisions ----------------------------------------------------------------------------------
@router.get("/api/rule-decisions", response_model=list[DecisionOut])
def decisions(
    account_id: int,
    status: str | None = None,
    moved_back: bool = False,
    limit: int = 100,
    before_id: int | None = None,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, account_id)
    q = (
        select(RuleDecision, MailMessage)
        .join(MailMessage, MailMessage.id == RuleDecision.message_id)
        .where(RuleDecision.account_id == account_id)
    )
    if status:
        q = q.where(RuleDecision.status == status)
    if moved_back:
        q = q.where(RuleDecision.moved_back_at.is_not(None))
    if before_id:
        q = q.where(RuleDecision.id < before_id)
    rows = db.execute(q.order_by(RuleDecision.id.desc()).limit(max(1, min(limit, 500)))).all()
    return [
        DecisionOut(
            id=d.id,
            message_id=m.id,
            thread_id=m.thread_id,
            subject=m.subject,
            from_addr=m.from_addr,
            date=m.date,
            rule_id=d.rule_id,
            rule_name=d.rule_name,
            how=d.how,
            why=d.why,
            model=d.model,
            status=d.status,
            reason=d.reason,
            actions=d.actions,
            moved_back=d.moved_back_at is not None,
            created_at=d.created_at,
        )
        for d, m in rows
    ]


@router.post("/api/rule-decisions/approve")
async def approve(
    body: IdsBody,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, body.account_id)
    out = await request.app.state.rule_decisions.approve(body.account_id, body.ids)
    _changed(request, body.account_id)
    return out


@router.post("/api/rule-decisions/reject")
def reject(
    body: IdsBody,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, body.account_id)
    n = request.app.state.rule_decisions.reject(body.account_id, body.ids)
    _changed(request, body.account_id)
    return {"rejected": n}


@router.post("/api/rule-decisions/{decision_id}/correct")
async def correct(
    decision_id: int,
    body: CorrectBody,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, body.account_id)
    try:
        new_id = await request.app.state.rule_decisions.correct(
            body.account_id, decision_id, body.rule_id
        )
    except WAIT_ERRORS as e:
        raise _wait(e) from e
    except PROBLEMS as e:
        raise _problem(e) from e
    _changed(request, body.account_id)
    return {"decision_id": new_id}


@router.post("/api/rule-decisions/undo")
async def undo(
    body: IdsBody,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, body.account_id)
    try:
        results = await request.app.state.rule_decisions.undo(body.account_id, body.ids)
    except WAIT_ERRORS as e:
        raise _wait(e) from e
    _changed(request, body.account_id)
    return [asdict(r) for r in results]


# ---- rule jobs ----------------------------------------------------------------------------------
@router.post("/api/rule-jobs", status_code=202)
def start_job(
    body: JobStartBody,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, body.account_id)
    try:
        jid = request.app.state.rule_jobs.start_preview(
            body.account_id, q=body.q, view=body.view, message_ids=body.message_ids
        )
    except PROBLEMS as e:
        raise _problem(e) from e
    return {"job_id": jid}


# Declared before /api/rule-jobs/{job_id}, so "open" isn't read as a job id.
@router.get("/api/rule-jobs/open", response_model=JobPreviewOut | None)
def open_job(
    account_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    """The mailbox's unfinished run of rules, so the page can show it again; null if none."""
    account_or_404(db, account_id)
    jobs = request.app.state.rule_jobs
    jid = jobs.open_job(account_id)
    return None if jid is None else _preview_out(request, jobs.preview(account_id, jid))


@router.get("/api/rule-jobs/{job_id}", response_model=JobPreviewOut)
def job(
    job_id: int,
    account_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, account_id)
    try:
        return _preview_out(request, request.app.state.rule_jobs.preview(account_id, job_id))
    except PROBLEMS as e:
        raise _problem(e) from e


# Async, so the run's task starts on the server's event loop (a worker thread has none).
@router.post("/api/rule-jobs/{job_id}/run", status_code=202)
async def run_job(
    job_id: int,
    body: JobRunBody,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, body.account_id)
    try:
        request.app.state.rule_jobs.run(body.account_id, job_id, body.confirm)
    except PROBLEMS as e:
        raise _problem(e) from e
    return {"job_id": job_id}


@router.post("/api/rule-jobs/{job_id}/stop", status_code=204)
async def stop_job(
    job_id: int,
    body: AccountBody,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, body.account_id)
    try:
        request.app.state.rule_jobs.stop(body.account_id, job_id)
    except PROBLEMS as e:
        raise _problem(e) from e
    return Response(status_code=204)
