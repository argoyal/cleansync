from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.pairs import load_pair
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.models import Conflict
from app.models.enums import ConflictState
from app.schemas.jobs import ConflictOut, ResolvedOut
from app.services.queue import is_stopped, stop_message
from app.services.sync.conflicts import ConflictError, resolve_conflict

router = APIRouter()


class ResolveIn(BaseModel):
    action: Literal["keep_local", "keep_remote", "keep_both", "skip"]


@router.get("/api/sync-pairs/{pair_id}/conflicts", response_model=list[ConflictOut])
def list_conflicts(
    pair_id: int,
    state: ConflictState | None = ConflictState.OPEN,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    load_pair(db, pair_id)
    q = select(Conflict).where(Conflict.pair_id == pair_id).order_by(Conflict.id)
    if state:
        q = q.where(Conflict.state == state)
    return [ConflictOut.model_validate(c, from_attributes=True) for c in db.scalars(q)]


# async: queueing the follow-up sync must happen on the event loop thread.
@router.post("/api/conflicts/{conflict_id}/resolve", response_model=ResolvedOut)
async def resolve(
    conflict_id: int,
    body: ResolveIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    conflict = db.get(Conflict, conflict_id)
    if conflict is None:
        raise HTTPException(404, "conflict not found")
    pair, account = load_pair(db, conflict.pair_id)
    queue = request.app.state.queue
    # Refuse before moving any files: with syncing disabled nothing would pick the result up.
    if await is_stopped(queue):  # checks the disk now
        raise HTTPException(503, stop_message(queue))
    if queue.is_running(pair.id):
        raise HTTPException(409, "this pair is busy; try again when the current job finishes")
    # Note: each resolution removes two conflict copies on Drive, so resolving many conflicts
    # can trip the deletion limit on the next sync.
    try:
        resolve_conflict(
            db, request.app.state.layout, account, pair, conflict, body.action, utcnow().date()
        )
    except ConflictError as e:
        raise HTTPException(409, str(e)) from e
    out = ResolvedOut.model_validate(conflict, from_attributes=True)
    if body.action != "skip":
        # "busy" is fine: the files are already resolved and the next sync will pick them up.
        out.sync = queue.request(pair.id, "RESOLVE")
    return out
