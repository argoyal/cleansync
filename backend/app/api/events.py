from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.auth.deps import get_db, require_user
from app.models import Event
from app.services.events.bus import event_stream, event_to_dict

router = APIRouter()


@router.get("/api/events", dependencies=[Depends(require_user)])
async def events(request: Request):
    return EventSourceResponse(
        event_stream(request.app.state.bus, request.is_disconnected), ping=15
    )


@router.get("/api/activity", dependencies=[Depends(require_user)])
def activity(
    limit: int = 50, pair_id: int | None = None, db: Session = Depends(get_db)
) -> list[dict]:
    q = select(Event).order_by(Event.id.desc()).limit(max(1, min(limit, 500)))
    if pair_id is not None:
        q = q.where(Event.pair_id == pair_id)
    return [event_to_dict(e) for e in db.scalars(q)]
