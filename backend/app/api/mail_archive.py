"""Delete forever (until Task 5 moves it to mail_forever.py)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.mail_reader import account_or_404
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.services.mail.archive import ArchiveProblem, delete_forever

router = APIRouter(prefix="/api/mail")


class ForeverIn(BaseModel):
    account_id: int
    message_ids: list[int] | None = Field(None, max_length=50000)
    q: str = Field("", max_length=1000)
    confirm: int


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
