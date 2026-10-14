"""Unsubscribe from mailing lists (0.5.2 §3, §4): the sender list, sender statuses and the
unsubscribe, auto-archive and keep jobs."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.mail_reader import account_or_404
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.models import UnsubscribeJob, UnsubscribeSender
from app.models.enums import UnsubscribeMethod
from app.schemas.unsubscribe import (
    ChangedOut,
    JobIn,
    JobOut,
    JobStartedOut,
    MarkIn,
    SendersOut,
)
from app.services.mail.header_scan import scan_progress
from app.services.mail.service import MailError
from app.services.unsubscribe.jobs import PICK_ONE_MSG, items_out, job_out
from app.services.unsubscribe.senders import (
    SORTS,
    TABS,
    list_senders,
    sender_addresses,
    set_status,
    suggested_count,
)

router = APIRouter(prefix="/api/mail/unsubscribe")


@router.get("/senders", response_model=SendersOut)
def senders(
    account_id: int,
    tab: str = "suggested",
    q: str = "",
    sort: str = "mails",
    page: int = 1,
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_user),
):
    acc = account_or_404(db, account_id)
    if tab not in TABS:
        raise HTTPException(400, "unknown tab")
    if sort not in SORTS:
        raise HTTPException(400, "unknown sort")
    items, total = list_senders(db, acc.id, acc.email, tab=tab, q=q, sort=sort, page=page)
    scanned, scan_total = scan_progress(db, acc.id)
    return {
        "items": [asdict(r) for r in items],
        "total": total,
        "suggested": suggested_count(db, acc.id, acc.email),
        "scan": {"scanned": scanned, "total": scan_total},
    }


@router.post("/mark", response_model=ChangedOut)
def mark(
    body: MarkIn,
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_user),
):
    """Mark senders unsubscribed (by hand, outside CleanSync), or clear their status."""
    acc = account_or_404(db, body.account_id)
    now = utcnow()
    method = UnsubscribeMethod.MANUAL.value if body.status else None
    changed = 0
    for address in dict.fromkeys(a.strip().lower() for a in body.addresses if a.strip()):
        row = db.scalar(
            select(UnsubscribeSender).where(
                UnsubscribeSender.account_id == acc.id, UnsubscribeSender.address == address
            )
        )
        if (row.status if row else None) == body.status:
            continue  # nothing to change; clearing a sender with no row makes no row
        set_status(db, acc.id, address, body.status, method=method, now=now, commit=False)
        changed += 1
    db.commit()
    return {"changed": changed}


# ---- jobs -------------------------------------------------------------------------------
BOTH_MSG = "Pick senders, or Pick all, not both."


def _job_detail(db: Session, job: UnsubscribeJob) -> dict:
    return {**job_out(db, job), "items": items_out(db, job.id)}


@router.post("/jobs", status_code=202, response_model=JobStartedOut)
async def start_job(
    body: JobIn,
    request: Request,
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_user),
):
    acc = account_or_404(db, body.account_id)
    if (body.addresses is None) == (body.all is None):
        raise HTTPException(400, BOTH_MSG)
    if body.all is not None:
        if body.all.tab not in TABS:
            raise HTTPException(400, "unknown tab")
        addresses = sender_addresses(db, acc.id, acc.email, tab=body.all.tab, q=body.all.q)
    else:
        addresses = body.addresses or []
    if not addresses:
        raise HTTPException(400, PICK_ONE_MSG)
    db.close()  # the job opens its own sessions; don't hold this one across the wait
    try:
        job_id = await request.app.state.unsubscribe_jobs.start(acc.id, body.kind, addresses)
    except MailError as e:
        raise HTTPException(e.status, str(e)) from e
    return {"job_id": job_id}


@router.get("/jobs", response_model=JobOut | None)
def latest_job(
    account_id: int,
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_user),
):
    acc = account_or_404(db, account_id)
    job = db.scalar(
        select(UnsubscribeJob)
        .where(UnsubscribeJob.account_id == acc.id)
        .order_by(UnsubscribeJob.id.desc())
        .limit(1)
    )
    return _job_detail(db, job) if job is not None else None


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_user),
):
    job = db.get(UnsubscribeJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return _job_detail(db, job)


@router.post("/jobs/{job_id}/stop", response_model=JobOut)
def stop_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_user),
):
    try:
        request.app.state.unsubscribe_jobs.stop(job_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    db.expire_all()
    return _job_detail(db, db.get(UnsubscribeJob, job_id))
