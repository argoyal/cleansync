"""Recovery (spec §8): list what DriveSync kept, restore copies, download."""

import asyncio
import os
import secrets
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
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
    ZipIn,
    ZipOut,
)
from app.services.app_settings import get_setting
from app.services.files.recovery import (
    RecoveryEntry,
    RecoveryError,
    check_filters,
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
CHUNK = 1024 * 1024
GONE_MSG = "This file is no longer in Recovery (it was cleaned up or removed)."
TOO_MANY_MSG = f"At most {MAX_ZIP} files can go in one download. Pick fewer."
ZIP_GONE_MSG = "This download has expired or was already used. Download the files again."
ZIP_TOKEN_SECONDS = 300


def _now() -> float:
    return time.monotonic()


@dataclass
class _ZipTicket:
    session_id: str
    files: list[tuple[Path, str]]
    expires: float


class ZipTickets:
    """Short-lived, one-use download tickets, so a big pick never has to fit in a URL.
    Kept in memory: a restart just makes the person click Download again."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, _ZipTicket] = {}

    def issue(self, session_id: str, files: list[tuple[Path, str]]) -> str:
        token = secrets.token_urlsafe(24)
        now = _now()
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v.expires > now}
            self._items[token] = _ZipTicket(session_id, files, now + ZIP_TOKEN_SECONDS)
        return token

    def take(self, token: str, session_id: str) -> list[tuple[Path, str]] | None:
        with self._lock:
            ticket = self._items.get(token)
            if ticket is None or ticket.session_id != session_id:
                return None
            del self._items[token]
        return ticket.files if ticket.expires > _now() else None


def _tickets(request: Request) -> ZipTickets:
    tickets = getattr(request.app.state, "recovery_zip_tickets", None)
    if tickets is None:
        tickets = request.app.state.recovery_zip_tickets = ZipTickets()
    return tickets


def _default_days(db: Session) -> int | None:
    days = get_setting(db, "default_backup_retention_days")
    return int(days) if isinstance(days, int | float | str) else None  # None: kept for good


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
    try:
        clean_path = check_filters(kind, path)
    except RecoveryError as e:
        raise HTTPException(e.status, str(e)) from e
    scope = get_scope(request, db, account_id)
    default_days = _default_days(db)
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
        default_days=default_days,
        offloads=rows,
    )
    chosen = filter_entries(entries, pair=pair, kind=kind, path=clean_path)
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


def _resolve(request: Request, db: Session, scope: Scope, entry_id: str):
    try:
        return resolve(request.app.state.layout, scope, entry_id, default_days=_default_days(db))
    except RecoveryError as e:
        raise HTTPException(e.status, str(e)) from e


@router.api_route("/file", methods=["GET", "HEAD"])
def download_file(
    account_id: int,
    id: str,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    scope = get_scope(request, db, account_id)
    entry, src = _resolve(request, db, scope, id)
    if request.method == "HEAD":  # the page checks the file is still there before downloading
        return Response(headers={"Cache-Control": "private, no-store"})
    # Opened here: if retention removes the file now, the open file still reads to the end;
    # if it was removed already, a plain 404.
    try:
        fh = open(src, "rb")  # noqa: SIM115 - closed by _read
    except FileNotFoundError:
        raise HTTPException(404, GONE_MSG) from None
    except OSError as e:
        raise HTTPException(500, f"could not read it: {e.strerror}") from e
    size = os.fstat(fh.fileno()).st_size
    return StreamingResponse(
        _read(fh),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": content_disposition("attachment", entry.name),
            "Content-Length": str(size),
            "Cache-Control": "private, no-store",
        },
    )


def _read(fh) -> Iterator[bytes]:
    with fh:
        while chunk := fh.read(CHUNK):
            yield chunk


@router.post("/zip", response_model=ZipOut)
def prepare_zip(
    body: ZipIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Checks every picked file is still there and hands back a one-use ticket for the zip."""
    if len(body.ids) > MAX_ZIP:
        raise HTTPException(422, TOO_MANY_MSG)
    scope = get_scope(request, db, body.account_id)
    days = _default_days(db)
    files: list[tuple[Path, str]] = []
    gone: list[str] = []
    for entry_id in dict.fromkeys(body.ids):
        try:
            entry, src = resolve(request.app.state.layout, scope, entry_id, default_days=days)
        except RecoveryError as e:
            if e.status != 404:
                raise HTTPException(e.status, str(e)) from e
            gone.append(entry_id)
            continue
        files.append((src, entry.name))
    if gone:
        n = len(gone)
        message = f"{n} of the picked items {'was' if n == 1 else 'were'} cleaned up meanwhile."
        raise HTTPException(409, {"message": message, "gone": gone})
    return ZipOut(token=_tickets(request).issue(ctx.session.id, files))


@router.get("/zip/{token}")
def download_zip(token: str, request: Request, ctx: AuthContext = Depends(require_user)):
    files = _tickets(request).take(token, ctx.session.id)
    if files is None:
        raise HTTPException(404, ZIP_GONE_MSG)
    # A file removed since the check stops the download (zip_files raises), never a broken zip.
    return StreamingResponse(
        zip_files(files),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition("attachment", "Recovery.zip"),
            "Cache-Control": "private, no-store",
        },
    )
