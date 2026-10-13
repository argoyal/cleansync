"""Mail offload and restore (spec 5)."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.mail_reader import account_or_404
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.models import MailMessage, MailOffload, MailOffloadItem
from app.models.enums import DecisionStatus, MailItemResult, MailState
from app.schemas.mail import (
    AccountIn,
    MailBriefOut,
    MailOffloadDetailOut,
    MailOffloadOut,
    MailPickIn,
    MailRecoveryOut,
    OffloadOneOut,
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
from app.services.rules.apply import WAIT_ERRORS

router = APIRouter()


def _pick(body: MailPickIn) -> Pick:
    return Pick(
        q=body.q,
        message_ids=body.message_ids,
        thread_ids=body.thread_ids,
        view=body.view,
        label=body.label or None,
    )


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


@router.get("/api/recovery/mail", response_model=MailRecoveryOut)
def recovery_mail(
    account_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Spec 3.4: Recovery shows mail offloads (newest first, a page at a time) and how many
    mails were deleted in Gmail, next to the Drive items."""
    account_or_404(db, account_id)
    offloads = db.scalars(
        select(MailOffload)
        .where(MailOffload.account_id == account_id, MailOffload.done > 0)
        .order_by(MailOffload.id.desc())
        .limit(limit)
        .offset(offset)
    )
    deleted = db.scalar(
        select(func.count())
        .select_from(MailMessage)
        .where(MailMessage.account_id == account_id, MailMessage.state == MailState.DELETED)
    )
    now = utcnow()
    return MailRecoveryOut(
        offloads=[offload_out(db, o, now) for o in offloads], deleted_count=deleted or 0
    )


@router.post("/api/mail/messages/{message_id}/offload", response_model=OffloadOneOut)
async def offload_one(
    message_id: int,
    body: AccountIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Offload on one mail in the reader (mail panel spec §8): checked, moved to Gmail's
    trash, kept here as offloaded. Through the applier, like a rule's Offload."""
    account_or_404(db, body.account_id)
    try:
        d = await request.app.state.rule_applier.offload_by_hand(body.account_id, message_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except WAIT_ERRORS as e:
        raise HTTPException(503, f"{e} Nothing was offloaded; try again later.") from e
    request.app.state.bus.publish("rules", {"account_id": body.account_id})
    request.app.state.bus.publish("mail", {"account_id": body.account_id})
    return OffloadOneOut(offloaded=d.status == DecisionStatus.APPLIED, reason=d.reason)
