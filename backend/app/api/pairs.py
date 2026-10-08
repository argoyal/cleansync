from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.models import Account, SyncPair
from app.models.enums import PairStatus
from app.schemas.pairs import PairCreate, PairOut, PairUpdate
from app.services.sync.pairs import PairError, create_pair, delete_pair, to_out, update_pair

router = APIRouter(prefix="/api/sync-pairs")


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
    pair.status = PairStatus.HEALTHY if pair.initialized else PairStatus.NEW
    db.commit()
    _reload_schedule(request)
    return to_out(request.app.state.layout, acc, pair)


def _reload_schedule(request: Request) -> None:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.reload()
