import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.core.pathsafe import UnsafePath, normalize_rel, safe_join
from app.models import SyncPair
from app.schemas.files import BrowseOut, PairBrief
from app.services.files.listing import PairView, list_folder
from app.services.files.scope import Scope, is_hidden, load_scope, pair_for, pairs_inside
from app.services.files.status import load_overlay

router = APIRouter()


def get_scope(request: Request, db: Session, account_id: int) -> Scope:
    scope = load_scope(db, request.app.state.layout, account_id, create_root=False)
    if scope is None:
        raise HTTPException(404, "account not found")
    return scope


def clean_path(path: str) -> str:
    try:
        rel = normalize_rel(path)
    except UnsafePath as e:
        raise HTTPException(422, str(e)) from e
    if is_hidden(rel):
        raise HTTPException(404, "not found")
    return rel


async def pair_views(
    request: Request, db: Session, scope: Scope, pairs: list[SyncPair]
) -> dict[int, PairView]:
    state = request.app.state
    loaded = await asyncio.gather(*(state.indexes.get(p, scope.account) for p in pairs))
    views: dict[int, PairView] = {}
    for pair, (index, catalog) in zip(pairs, loaded, strict=True):
        overlay = load_overlay(db, pair.id, state.queue.is_running(pair.id))
        views[pair.id] = PairView(index=index, catalog=catalog, overlay=overlay)
    return views


def _open_folder(scope: Scope, rel: str) -> bool:
    """Disk checks for a browse path (runs in a worker thread)."""
    if rel == "":
        scope.root.mkdir(parents=True, exist_ok=True)
    return safe_join(scope.root, rel).is_dir()


@router.get("/api/browse", response_model=BrowseOut)
async def browse(
    account_id: int,
    request: Request,
    path: str = "",
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    scope = get_scope(request, db, account_id)
    rel = clean_path(path)
    try:
        is_folder = await asyncio.to_thread(_open_folder, scope, rel)
    except UnsafePath as e:
        raise HTTPException(422, str(e)) from e
    if not is_folder:
        raise HTTPException(404, "folder not found")
    pair = pair_for(scope.pairs, rel)
    views = await pair_views(request, db, scope, [pair] if pair else pairs_inside(scope.pairs, rel))
    items = await asyncio.to_thread(list_folder, scope, rel, views)
    brief = (
        PairBrief(
            id=pair.id,
            name=pair.name,
            status=pair.status,
            initialized=pair.initialized,
            local_subpath=pair.local_subpath,
        )
        if pair
        else None
    )
    return BrowseOut(account_id=scope.account.id, path=rel, pair=brief, items=items)
