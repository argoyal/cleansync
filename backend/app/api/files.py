import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.core.pathsafe import UnsafePath, normalize_rel, safe_join
from app.models import SyncPair
from app.schemas.files import (
    BrowseOut,
    DiskUsage,
    DriveQuota,
    FileItemOut,
    PairBrief,
    StorageOut,
)
from app.services.files.listing import PairView, list_folder
from app.services.files.scope import Scope, is_hidden, load_scope, pair_for, pairs_inside
from app.services.files.status import load_overlay
from app.services.files.views import recent, search, starred
from app.services.offload.store import excluded_paths

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
    excluded = {p.id: excluded_paths(db, p.id) for p in pairs}
    loaded = await asyncio.gather(
        *(state.indexes.get(p, scope.account, excluded[p.id]) for p in pairs)
    )
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
            direction=pair.direction,
        )
        if pair
        else None
    )
    return BrowseOut(account_id=scope.account.id, path=rel, pair=brief, items=items)


@router.get("/api/recent", response_model=list[FileItemOut])
async def recent_files(
    account_id: int,
    request: Request,
    limit: int = 50,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    scope = get_scope(request, db, account_id)
    views = await pair_views(request, db, scope, scope.pairs)
    return await asyncio.to_thread(recent, scope, views, max(1, min(limit, 200)))


@router.get("/api/starred", response_model=list[FileItemOut])
async def starred_files(
    account_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    scope = get_scope(request, db, account_id)
    views = await pair_views(request, db, scope, scope.pairs)
    return await asyncio.to_thread(starred, scope, views)


@router.get("/api/search", response_model=list[FileItemOut])
async def search_files(
    account_id: int,
    q: str,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(422, "type at least 2 characters")
    scope = get_scope(request, db, account_id)
    views = await pair_views(request, db, scope, scope.pairs)
    return await asyncio.to_thread(search, scope, views, query[:200])


def _disk_usage(root: Path):
    """Disk use of the volume holding the account folder (which may not exist yet)."""
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe)


@router.get("/api/storage", response_model=StorageOut)
async def storage(
    account_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    scope = get_scope(request, db, account_id)
    usage = await asyncio.to_thread(_disk_usage, scope.root)
    # synced_bytes comes from the cached status index, so it can lag by about a minute.
    views = await pair_views(request, db, scope, scope.pairs)
    acc = scope.account
    return StorageOut(
        account_id=acc.id,
        email=acc.email,
        drive=DriveQuota(
            total=acc.quota_total,
            used=acc.quota_used,
            trash=acc.quota_trash,
            checked_at=acc.last_checked_at,
        ),
        disk=DiskUsage(total=usage.total, used=usage.used, free=usage.free),
        synced_bytes=sum(v.index.total_bytes for v in views.values()) if views else None,
    )
