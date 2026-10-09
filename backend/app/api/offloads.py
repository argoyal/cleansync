"""Offload (spec §7.2, §7.2a): preview, run in the background, list, restore, empty trash.

Every step that changes Drive checks the missing-disk stop and the pair's queue first, inside
OffloadService (start, restore, empty_trash): nothing here reaches Drive on its own."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.files import clean_path, get_scope
from app.auth.deps import AuthContext, get_db, require_user
from app.models import Offload, SyncPair
from app.models.enums import OffloadState
from app.schemas.offloads import (
    BatchItemOut,
    BatchOut,
    OffloadIn,
    OffloadOut,
    PreviewItemOut,
    PreviewOut,
)
from app.services.files.scope import to_account_rel
from app.services.offload.service import OffloadBatch, OffloadError
from app.services.queue import is_stopped, stop_message

router = APIRouter(prefix="/api/offloads")


def _http(e: OffloadError) -> HTTPException:
    return HTTPException(e.status, str(e))


def _batch(batch: OffloadBatch) -> BatchOut:
    return BatchOut(
        id=batch.id,
        account_id=batch.account_id,
        done=batch.done,
        items=[BatchItemOut(**asdict(i)) for i in batch.items],
    )


def offload_out(db: Session, off: Offload, emptying: set[int]) -> OffloadOut:
    pair = db.get(SyncPair, off.pair_id)
    return OffloadOut(
        id=off.id,
        pair_id=pair.id,
        pair_name=pair.name,
        account_id=pair.account_id,
        path=to_account_rel(pair, off.path),
        is_dir=off.is_dir,
        file_count=off.file_count,
        total_bytes=off.total_bytes,
        state=off.state,
        trash_verified=off.trash_verified,
        trash_emptied=off.trash_emptied,
        created_at=off.created_at,
        updated_at=off.updated_at,
        note=off.note,
        busy=off.id in emptying,
    )


@router.post("/preview", response_model=PreviewOut)
async def preview(
    body: OffloadIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    scope = get_scope(request, db, body.account_id)
    rels = [clean_path(p) for p in body.paths]
    try:
        items = await request.app.state.offloads.preview(scope.account.id, rels)
    except OffloadError as e:
        raise _http(e) from e
    queue = request.app.state.queue
    stopped = stop_message(queue) if await is_stopped(queue) else None
    ready = [i for i in items if i.blocked is None]
    return PreviewOut(
        items=[
            PreviewItemOut(
                path=i.path,
                pair_id=i.pair.id if i.pair else None,
                pair_name=i.pair.name if i.pair else None,
                is_dir=i.is_dir,
                file_count=i.file_count,
                total_bytes=i.total_bytes,
                stays_on_drive=i.stays_on_drive,
                blocked=i.blocked,
            )
            for i in items
        ],
        total_files=sum(i.file_count for i in ready),
        total_bytes=sum(i.total_bytes for i in ready),
        stopped=stopped,
    )


@router.post("", response_model=BatchOut, status_code=202)
async def start(
    body: OffloadIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    scope = get_scope(request, db, body.account_id)
    rels = [clean_path(p) for p in body.paths]
    try:
        batch = await request.app.state.offloads.start(scope.account.id, rels)
    except OffloadError as e:
        raise _http(e) from e
    return _batch(batch)


@router.get("/batches/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: str, request: Request, ctx: AuthContext = Depends(require_user)):
    batch = request.app.state.offloads.batch(batch_id)
    if batch is None:
        raise HTTPException(404, "this offload is no longer listed")
    return _batch(batch)


@router.get("", response_model=list[OffloadOut])
def list_offloads(
    account_id: int,
    request: Request,
    include_restored: bool = False,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    get_scope(request, db, account_id)  # 404 for an unknown account
    q = (
        select(Offload)
        .join(SyncPair, SyncPair.id == Offload.pair_id)
        .where(SyncPair.account_id == account_id)
        .order_by(Offload.id.desc())
    )
    if not include_restored:
        q = q.where(Offload.state != OffloadState.RESTORED)
    emptying = request.app.state.offloads.emptying
    return [offload_out(db, off, emptying) for off in db.scalars(q)]


async def _act(request: Request, db: Session, offload_id: int, action) -> OffloadOut:
    try:
        await action(offload_id)
    except OffloadError as e:
        raise _http(e) from e
    db.expire_all()
    return offload_out(db, db.get(Offload, offload_id), request.app.state.offloads.emptying)


@router.post("/{offload_id}/restore", response_model=OffloadOut)
async def restore(
    offload_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    return await _act(request, db, offload_id, request.app.state.offloads.restore)


@router.post("/{offload_id}/empty-trash", response_model=OffloadOut, status_code=202)
async def empty_trash(
    offload_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    return await _act(request, db, offload_id, request.app.state.offloads.empty_trash)
