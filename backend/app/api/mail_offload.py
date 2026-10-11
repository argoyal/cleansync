"""Mail offload and restore (spec 5)."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.models import MailMessage, MailOffload, MailOffloadItem
from app.models.enums import MailItemResult
from app.schemas.mail import (
    MailBriefOut,
    MailOffloadDetailOut,
    MailOffloadOut,
    MailPickIn,
    OffloadPreviewOut,
    OffloadStartedOut,
    OffloadStartIn,
    RestoreMailIn,
    RestoreQueuedOut,
    SkippedOut,
)
from app.services.mail.offload import offload_out
from app.services.mail.offload_plan import Pick
from app.services.mail.service import MailError

router = APIRouter()


def _pick(body: MailPickIn) -> Pick:
    return Pick(q=body.q, message_ids=body.message_ids, thread_ids=body.thread_ids)


def _offloads(request: Request):
    return request.app.state.mail_offloads


@router.post("/api/mail/offloads/preview", response_model=OffloadPreviewOut)
async def preview(body: MailPickIn, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        p = await _offloads(request).preview(body.account_id, _pick(body))
    except MailError as e:
        raise HTTPException(e.status, str(e)) from e
    return OffloadPreviewOut(
        filter_text=p.filter_text,
        count=p.count,
        bytes=p.bytes,
        largest=[MailBriefOut(**asdict(m)) for m in p.largest],
        oldest=[MailBriefOut(**asdict(m)) for m in p.oldest],
        excluded=p.excluded,
    )


@router.post("/api/mail/offloads", status_code=202, response_model=OffloadStartedOut)
async def start(body: OffloadStartIn, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        off_id = await _offloads(request).start(body.account_id, _pick(body), body.confirm)
    except MailError as e:
        raise HTTPException(e.status, str(e)) from e
    return OffloadStartedOut(offload_id=off_id)


@router.get("/api/mail/offloads", response_model=list[MailOffloadOut])
def list_offloads(
    account_id: int, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(MailOffload)
        .where(MailOffload.account_id == account_id)
        .order_by(MailOffload.id.desc())
    )
    now = utcnow()
    return [offload_out(db, o, now) for o in rows]


@router.get("/api/mail/offloads/{offload_id}", response_model=MailOffloadDetailOut)
def get_offload(
    offload_id: int, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)
):
    off = db.get(MailOffload, offload_id)
    if off is None:
        raise HTTPException(404, "offload not found")
    skipped = db.execute(
        select(
            MailOffloadItem.reason,
            MailMessage.id,
            MailMessage.subject,
            MailMessage.from_addr,
            MailMessage.date,
        )
        .join(MailMessage, MailMessage.id == MailOffloadItem.message_id)
        .where(
            MailOffloadItem.offload_id == offload_id,
            MailOffloadItem.result == MailItemResult.SKIPPED,
        )
        .order_by(MailOffloadItem.id)
        .limit(500)
    ).all()
    return MailOffloadDetailOut(
        **offload_out(db, off, utcnow()),
        skipped_items=[
            SkippedOut(message_id=mid, subject=subj, from_addr=frm, date=when, reason=reason or "")
            for reason, mid, subj, frm, when in skipped
        ],
    )


@router.post("/api/mail/restore", status_code=202, response_model=RestoreQueuedOut)
async def restore(body: RestoreMailIn, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        queued, count = await _offloads(request).request_restore(
            body.account_id, _pick(body), body.offload_id
        )
    except MailError as e:
        raise HTTPException(e.status, str(e)) from e
    return RestoreQueuedOut(queued=queued, count=count)
