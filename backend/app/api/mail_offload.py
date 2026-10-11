"""Mail offload and restore (spec 5)."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.deps import AuthContext, require_user
from app.schemas.mail import MailBriefOut, MailPickIn, OffloadPreviewOut
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
