"""Delete forever in Offloaded and Deleted in Gmail (mail panel spec §7)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.mail_reader import account_or_404
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.services.mail.forever import ForeverProblem, delete_forever

router = APIRouter(prefix="/api/mail")


class ForeverIn(BaseModel):
    """Picked mails (message_ids), or a pick-all: the view, label and search."""

    account_id: int
    view: str = Field(max_length=32)
    label: str | None = Field(None, max_length=128)
    q: str = Field("", max_length=1000)
    message_ids: list[int] | None = Field(None, max_length=50000)
    confirm: int


@router.post("/delete-forever")
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
            view=body.view,
            label=body.label or None,
            q=body.q,
            message_ids=body.message_ids,
            confirm=body.confirm,
            now=utcnow(),
            restore_queued=lambda aid: any(a == "restore" for a, _ in queue.waiting(aid)),
        )
    except ForeverProblem as e:
        raise HTTPException(400, str(e)) from e
    request.app.state.bus.publish("mail", {"account_id": acc.id})
    return {"deleted": res.deleted, "left": res.left}
