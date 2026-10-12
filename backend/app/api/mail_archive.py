"""Delete (to Gmail's trash and the local archive) and Delete forever (AI rules spec §7)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.mail_reader import account_or_404
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.models import MailMessage
from app.models.enums import MailState
from app.services.mail.archive import ArchiveProblem, delete_forever
from app.services.rules.apply import WAIT_ERRORS

router = APIRouter(prefix="/api/mail")


class DeleteIn(BaseModel):
    account_id: int
    message_ids: list[int] = Field(default_factory=list, max_length=500)
    thread_ids: list[str] = Field(default_factory=list, max_length=500)  # every mail in them


class ForeverIn(BaseModel):
    account_id: int
    message_ids: list[int] | None = Field(None, max_length=50000)
    q: str = Field("", max_length=1000)
    confirm: int


@router.post("/delete")
async def delete(
    body: DeleteIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, body.account_id)
    ids = list(body.message_ids)
    if body.thread_ids:
        ids += list(
            db.scalars(
                select(MailMessage.id).where(
                    MailMessage.account_id == body.account_id,
                    MailMessage.thread_id.in_(body.thread_ids),
                    MailMessage.state == MailState.IN_MAIL,
                )
            )
        )
    ids = list(dict.fromkeys(ids))
    if not ids or len(ids) > 500:
        raise HTTPException(400, "Pick between 1 and 500 mails to delete. For more, use a rule.")
    applier = request.app.state.rule_applier
    deleted, failed = 0, []
    for mid in ids:
        try:
            d = await applier.delete_by_hand(body.account_id, mid)
        except LookupError as e:
            failed.append({"message_id": mid, "reason": str(e)})
            continue
        except WAIT_ERRORS as e:
            raise HTTPException(503, f"{e} {deleted} deleted so far; try the rest later.") from e
        if d.status == "applied":
            deleted += 1
        else:
            failed.append({"message_id": mid, "reason": d.reason or "It wasn't deleted."})
    request.app.state.bus.publish("rules", {"account_id": body.account_id})
    return {"deleted": deleted, "failed": failed}


@router.post("/archive/delete-forever")
def forever(
    body: ForeverIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    acc = account_or_404(db, body.account_id)
    queue = request.app.state.mail.queue
    try:
        res = delete_forever(
            db,
            request.app.state.layout,
            request.app.state.bus,
            acc.id,
            acc.email,
            message_ids=body.message_ids,
            q=body.q,
            confirm=body.confirm,
            now=utcnow(),
            restore_queued=lambda aid: any(a == "restore" for a, _ in queue.waiting(aid)),
        )
    except ArchiveProblem as e:
        raise HTTPException(400, str(e)) from e
    return {"deleted": res.deleted, "left": res.left}
