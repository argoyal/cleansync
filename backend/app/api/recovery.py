"""Recovery (spec §8): list what DriveSync kept, restore copies, download."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.fileops import after_change, refuse_when_disabled
from app.api.files import get_scope
from app.api.serve import content_disposition
from app.auth.deps import AuthContext, get_db, require_user
from app.models import Offload, SyncPair
from app.models.enums import OffloadState
from app.schemas.recovery import (
    RecoveryEntryOut,
    RecoveryListOut,
    RestoreIn,
    RestoreOut,
    RestoreResultOut,
)
from app.services.app_settings import get_setting
from app.services.files.recovery import (
    RecoveryEntry,
    RecoveryError,
    filter_entries,
    list_entries,
    resolve,
    restore_entries,
)
from app.services.files.scope import Scope
from app.services.files.zipstream import zip_files
from app.services.offload.store import open_paths

router = APIRouter(prefix="/api/recovery")
MAX_ZIP = 1000


def _out(e: RecoveryEntry) -> RecoveryEntryOut:
    return RecoveryEntryOut(
        id=e.id,
        kind=e.kind,
        pair_id=e.pair_id,
        pair_name=e.pair_name,
        name=e.name,
        original_path=e.original_path,
        size=e.size,
        day=e.day,
        expires_on=e.expires_on,
        restorable=e.restorable,
        offload_id=e.offload_id,
    )


@router.get("", response_model=RecoveryListOut)
async def list_recovery(
    account_id: int,
    request: Request,
    pair: str = "",
    kind: str = "",
    path: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    scope = get_scope(request, db, account_id)
    default_days = get_setting(db, "default_backup_retention_days")
    # Files DriveSync sent to Drive's trash in an offload, still there.
    offloads = db.execute(
        select(Offload, SyncPair)
        .join(SyncPair, SyncPair.id == Offload.pair_id)
        .where(
            SyncPair.account_id == account_id,
            Offload.state.in_([OffloadState.ACTIVE, OffloadState.ATTENTION]),
            Offload.trash_emptied.is_(False),
        )
    ).all()
    rows = [(o, p) for o, p in offloads if o.trashed_ids]
    entries = await asyncio.to_thread(
        list_entries,
        request.app.state.layout,
        scope,
        default_days=default_days,  # type: ignore[arg-type]
        offloads=rows,
    )
    chosen = filter_entries(entries, pair=pair, kind=kind, path=path.strip("/"))
    return RecoveryListOut(
        total=len(chosen), entries=[_out(e) for e in chosen[offset : offset + limit]]
    )


@router.post("/restore", response_model=RestoreOut)
async def restore(
    body: RestoreIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    await refuse_when_disabled(request)  # nothing would carry it to Drive
    scope = get_scope(request, db, body.account_id)
    # Read here: the database session stays on this thread.
    offloaded = {p.id: open_paths(db, p.id) for p in scope.pairs}
    results = await asyncio.to_thread(
        restore_entries, request.app.state.layout, scope, body.ids, offloaded=offloaded
    )
    done = [r.restored_to for r in results if r.restored_to]
    syncs = after_change(request, scope, done) if done else {}
    return RestoreOut(
        results=[
            RestoreResultOut(id=r.id, restored_to=r.restored_to, error=r.error) for r in results
        ],
        syncs=syncs,
    )


def _resolve(request: Request, scope: Scope, entry_id: str):
    try:
        return resolve(request.app.state.layout, scope, entry_id)
    except RecoveryError as e:
        raise HTTPException(e.status, str(e)) from e


@router.get("/file")
def download_file(
    account_id: int,
    id: str,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    scope = get_scope(request, db, account_id)
    entry, src = _resolve(request, scope, id)
    return FileResponse(
        src,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": content_disposition("attachment", entry.name),
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/zip")
def download_zip(
    account_id: int,
    request: Request,
    ids: list[str] = Query(...),
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    if len(ids) > MAX_ZIP:
        raise HTTPException(422, f"at most {MAX_ZIP} files in one download")
    scope = get_scope(request, db, account_id)
    files = []
    for entry_id in dict.fromkeys(ids):
        entry, src = _resolve(request, scope, entry_id)
        files.append((src, entry.name))
    return StreamingResponse(
        zip_files(files),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition("attachment", "Recovery.zip"),
            "Cache-Control": "private, no-store",
        },
    )
