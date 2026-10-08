from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.models import Account, SyncPair
from app.models.enums import AttentionReason, Direction, PairStatus
from app.schemas.pairs import PairCreate, PairOut, PairUpdate
from app.services.sync.pairs import PairError, create_pair, delete_pair, to_out, update_pair

router = APIRouter(prefix="/api/sync-pairs")

_ATTENTION = {"BLOCKED", "CONFLICTS", "NEEDS_RESYNC", "AUTH", "PLAN_UNREADABLE"}
_ERRORS = {"ACCESS_CHECK", "FAILED"}


def load_pair(db: Session, pair_id: int) -> tuple[SyncPair, Account]:
    pair = db.get(SyncPair, pair_id)
    if pair is None:
        raise HTTPException(404, "sync pair not found")
    return pair, db.get(Account, pair.account_id)


@router.get("", response_model=list[PairOut])
def list_pairs(
    request: Request, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)
):
    layout = request.app.state.layout
    return [
        to_out(layout, db.get(Account, p.account_id), p)
        for p in db.scalars(select(SyncPair).order_by(SyncPair.id))
    ]


@router.post("", response_model=PairOut, status_code=201)
def create(
    body: PairCreate,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        pair = create_pair(db, request.app.state.layout, body)
    except PairError as e:
        raise HTTPException(422, str(e)) from e
    _reload_schedule(request)
    return to_out(request.app.state.layout, db.get(Account, pair.account_id), pair)


@router.get("/{pair_id}", response_model=PairOut)
def get_pair(
    pair_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    pair, acc = load_pair(db, pair_id)
    return to_out(request.app.state.layout, acc, pair)


@router.put("/{pair_id}", response_model=PairOut)
def update(
    pair_id: int,
    body: PairUpdate,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    pair, acc = load_pair(db, pair_id)
    update_pair(db, pair, body)
    _reload_schedule(request)
    return to_out(request.app.state.layout, acc, pair)


@router.delete("/{pair_id}", status_code=204)
def delete(
    pair_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    pair, _ = load_pair(db, pair_id)
    queue = getattr(request.app.state, "queue", None)
    if queue is not None and queue.is_busy(pair_id):
        raise HTTPException(409, "this pair is syncing or queued; try again when it finishes")
    delete_pair(db, request.app.state.layout, pair)
    _reload_schedule(request)
    return Response(status_code=204)


@router.post("/{pair_id}/pause", response_model=PairOut)
def pause(
    pair_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    pair, acc = load_pair(db, pair_id)
    pair.paused = True
    pair.status = PairStatus.PAUSED
    db.commit()
    _reload_schedule(request)
    return to_out(request.app.state.layout, acc, pair)


@router.post("/{pair_id}/resume", response_model=PairOut)
def resume(
    pair_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    pair, acc = load_pair(db, pair_id)
    pair.paused = False
    if pair.attention_reason in _ATTENTION:
        pair.status = PairStatus.ATTENTION
    elif pair.attention_reason in _ERRORS:
        pair.status = PairStatus.ERROR
    else:
        pair.status = PairStatus.HEALTHY if pair.initialized else PairStatus.NEW
    db.commit()
    _reload_schedule(request)
    return to_out(request.app.state.layout, acc, pair)


def _reload_schedule(request: Request) -> None:
    for name in ("scheduler", "watcher"):
        svc = getattr(request.app.state, name, None)
        if svc is not None:
            svc.reload()


# Routes that queue work are async: SyncQueue must be called on the event loop thread.


class ConfirmIn(BaseModel):
    phrase: str


def _queue(request: Request, pair_id: int, trigger: str, action: str = "sync") -> dict:
    result = request.app.state.queue.request(pair_id, trigger, action)
    if result == "busy":
        raise HTTPException(409, "this pair is busy; try again when the current job finishes")
    if result == "disabled":
        raise HTTPException(
            503, "database integrity check failed; restore a backup (see docs/backup-recovery.md)"
        )
    return {"result": result}


@router.post("/{pair_id}/sync", status_code=202)
async def sync_now(
    pair_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    pair, _ = load_pair(db, pair_id)
    if not pair.initialized:
        raise HTTPException(409, "run the first-sync preview and confirm it first")
    return _queue(request, pair_id, "MANUAL")


@router.post("/{pair_id}/override-once", status_code=202)
async def override_once(
    pair_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    pair, _ = load_pair(db, pair_id)
    if not pair.initialized:
        raise HTTPException(409, "pair is not initialized")
    if pair.attention_reason != AttentionReason.BLOCKED:
        raise HTTPException(409, "nothing is blocked")
    if request.app.state.queue.is_running(pair_id):
        raise HTTPException(409, "this pair is busy; try again when the current job finishes")
    pair.override_next = True
    db.commit()
    try:
        return _queue(request, pair_id, "MANUAL")
    except HTTPException:
        pair.override_next = False  # nothing was queued: don't leave an approval lying around
        db.commit()
        raise


@router.post("/{pair_id}/preview", status_code=202)
async def start_preview(
    pair_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    pair, _ = load_pair(db, pair_id)
    if pair.initialized:
        raise HTTPException(409, "pair is already initialized")
    return _queue(request, pair_id, "INITIAL", "preview")


@router.get("/{pair_id}/preview")
def get_preview(
    pair_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    load_pair(db, pair_id)
    preview = request.app.state.initial.load_preview(pair_id)
    if preview is None:
        raise HTTPException(404, "no preview yet")
    return preview


@router.post("/{pair_id}/confirm-initial", status_code=202)
async def confirm_initial(
    pair_id: int,
    body: ConfirmIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    pair, _ = load_pair(db, pair_id)
    if pair.initialized:
        raise HTTPException(409, "pair is already initialized")
    initial = request.app.state.initial
    if initial.load_preview(pair_id) is None:
        raise HTTPException(409, "run the preview and review it first")
    try:
        initial.check_phrase(body.phrase)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return _queue(request, pair_id, "INITIAL", "initial")


@router.post("/{pair_id}/resync", status_code=202)
async def resync(
    pair_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    pair, _ = load_pair(db, pair_id)
    if not pair.initialized:
        raise HTTPException(409, "use the first-sync preview instead")
    if pair.direction != Direction.BIDIRECTIONAL:
        raise HTTPException(409, "one-way pairs don't need a resync")
    return _queue(request, pair_id, "MANUAL", "resync")
