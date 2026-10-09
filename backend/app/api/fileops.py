"""Changing files from the UI. Each action changes the local copy (and, for renames and moves
of items already on Google Drive, Drive too), then queues a sync."""

import asyncio
import errno
import logging
import os
import shutil
from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from app.api.files import clean_path, get_scope
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.core.pathsafe import UnsafePath
from app.models.enums import AccountStatus
from app.schemas.files import (
    DeleteIn,
    MkdirIn,
    MoveIn,
    NewFileIn,
    NewFileOut,
    OpOut,
    RenameIn,
    UploadOut,
)
from app.services.catalog.model import EXPORT_EXT, CatalogEntry, google_url
from app.services.drive_api.client import DEFAULT_NAMES
from app.services.drive_api.tokens import (
    DriveApiError,
    DriveAuthError,
    DriveForbiddenError,
    DriveNotFoundError,
)
from app.services.files import moves, ops
from app.services.files.names import validate_name
from app.services.files.scope import Scope, join_rel, pair_for, to_pair_rel
from app.services.files.status import load_overlay
from app.services.files.upload import new_temp, plan_target, publish, save_stream
from app.services.offload.store import IN_OFFLOADED_MSG, covers, has_inside, open_paths
from app.services.queue import is_stopped, stop_message

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files")
OFFICE_KINDS = ("docx", "xlsx", "pptx")
OFFLOADED_MSG = (
    "This is offloaded (kept only on this server). Restore it to Drive first, then rename or "
    "move it."
)


async def refuse_when_disabled(request: Request) -> None:
    # With syncing switched off nothing would carry the change to Drive: refuse up front.
    queue = request.app.state.queue
    # Checks the disk now, so the first action after it drops out never reaches Drive.
    if await is_stopped(queue):
        raise HTTPException(503, stop_message(queue))


def refuse_offloaded(
    db: Session,
    scope: Scope,
    rels: list[str],
    *,
    inside: bool = True,
    message: str = OFFLOADED_MSG,
) -> None:
    """Offloaded items (and folders holding them) keep their place until they are restored:
    moving them would put them back in sync under a new name, or out of DriveSync's sight.
    With inside=False only the item itself and what is inside an offloaded folder count."""
    for rel in rels:
        pair = pair_for(scope.pairs, rel)
        if pair is None:
            continue
        paths = open_paths(db, pair.id)
        prel = to_pair_rel(pair, rel)
        if covers(paths, prel) or (inside and has_inside(paths, prel)):
            raise HTTPException(409, message)


def refuse_adding_to_offloaded(db: Session, scope: Scope, folder: str) -> None:
    """New items can't go into an offloaded folder: they would never reach Google Drive (a
    new Google Doc would even bring the folder back on Drive)."""
    refuse_offloaded(db, scope, [folder], inside=False, message=IN_OFFLOADED_MSG)


def os_error(e: OSError) -> HTTPException:
    if e.errno == errno.ENOSPC:
        return HTTPException(507, "the server disk is full")
    if e.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return HTTPException(
            403, "DriveSync is not allowed to change this (check folder permissions)"
        )
    log.warning("file action failed: %s", e.strerror)
    return HTTPException(500, f"the file action failed: {e.strerror}")


def _http(e: Exception) -> HTTPException:
    if isinstance(e, ops.PartialError):
        inner = _http(e.cause)
        detail = {
            "message": inner.detail,
            "done": e.done,  # paths as sent: these items were changed
            "failed": e.failed,
            "not_done": e.not_done,  # left as they were
        }
        return HTTPException(inner.status_code, detail)
    if isinstance(e, ops.UndoFailed):
        inner = _http(e.cause)
        return HTTPException(inner.status_code, f"{inner.detail} {ops.UNDO_FAILED}")
    if isinstance(e, UnsafePath):
        return HTTPException(422, str(e))
    if isinstance(e, ops.FileOpError):
        return HTTPException(e.status, str(e))
    if isinstance(e, OSError):
        return os_error(e)
    log.exception("file action failed unexpectedly", exc_info=e)
    return HTTPException(500, "the file action failed unexpectedly")


async def guarded(awaitable):
    try:
        return await awaitable
    except Exception as e:
        raise _http(e) from e


async def run_op(fn, *args):
    return await guarded(asyncio.to_thread(fn, *args))


def after_change(
    request: Request,
    scope: Scope,
    rels: list[str],
    refresh: Iterable[int] = (),
    *,
    keep_index: bool = False,
) -> dict[int, str]:
    """Queue a sync for every touched pair (once, after a burst), tell open tabs, and drop
    cached statuses (right away unless keep_index: the listing judges new files itself)."""
    state = request.app.state
    changes = state.file_changes
    syncs: dict[int, str] = {}
    for pair_id in refresh:  # moved on Drive: list it again so the browser shows it there
        state.catalog.request_refresh(pair_id)
    for rel in rels:
        pair = pair_for(scope.pairs, rel)
        if pair is None or pair.id in syncs:
            continue
        if not keep_index:
            state.indexes.invalidate(pair.id)
        syncs[pair.id] = changes.request_sync(pair.id, initialized=pair.initialized)
    changes.notify(scope.account.id)
    return syncs


def open_conflicts(db: Session, pair_ids: Iterable[int]) -> dict[int, set[str]]:
    return {pid: load_overlay(db, pid, False).conflicts for pid in pair_ids}


async def update_listing(request: Request, changes: dict) -> None:
    """Make the cached Drive listing match what was just done (see apply_local_changes)."""
    store = request.app.state.catalog.store
    for pair_id, (removed, moved) in changes.items():
        try:
            await asyncio.shield(
                asyncio.to_thread(store.apply_local_changes, pair_id, removed, moved)
            )
        except Exception:  # the next full listing corrects it anyway
            log.exception("could not update the cached Drive listing of pair %s", pair_id)


async def _move(request: Request, db: Session, scope: Scope, steps: list[ops.MoveStep]) -> OpOut:
    state = request.app.state
    pair_ids = {s.pair.id for s in steps if s.pair is not None}
    plan = await guarded(
        moves.prepare_moves(
            scope,
            steps,
            queue=state.queue,
            catalog=state.catalog,
            drive=state.drive_mover,
            conflicts=open_conflicts(db, pair_ids),
        )
    )
    try:
        moved = await guarded(moves.run_moves(plan, queue=state.queue, drive=state.drive_mover))
    finally:
        # Also after a failure part-way: earlier items may have moved already.
        await update_listing(request, plan.listing_changes())
        syncs = after_change(request, scope, plan.touched, plan.drive_pairs)
    return OpOut(paths=moved, syncs=syncs, drive=plan.moved_on_drive)


@router.post("/mkdir", response_model=OpOut)
async def mkdir(
    body: MkdirIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    await refuse_when_disabled(request)
    scope = get_scope(request, db, body.account_id)
    refuse_adding_to_offloaded(db, scope, clean_path(body.path))
    rel = await run_op(ops.make_folder, scope, clean_path(body.path), body.name)
    return OpOut(paths=[rel], syncs=after_change(request, scope, [rel]))


@router.post("/rename", response_model=OpOut)
async def rename(
    body: RenameIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    await refuse_when_disabled(request)
    scope = get_scope(request, db, body.account_id)
    refuse_offloaded(db, scope, [clean_path(body.path)])
    step = await run_op(ops.plan_rename, scope, clean_path(body.path), body.new_name)
    return await _move(request, db, scope, [step])


@router.post("/move", response_model=OpOut)
async def move(
    body: MoveIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    await refuse_when_disabled(request)
    scope = get_scope(request, db, body.account_id)
    sources = [clean_path(p) for p in body.paths]
    refuse_offloaded(db, scope, sources)
    refuse_offloaded(db, scope, [clean_path(body.to)], inside=False)
    steps = await run_op(ops.plan_move, scope, sources, clean_path(body.to))
    return await _move(request, db, scope, steps)


@router.post("/delete", response_model=OpOut)
async def delete(
    body: DeleteIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    await refuse_when_disabled(request)
    scope = get_scope(request, db, body.account_id)
    rels = ops.prune_selection([clean_path(p) for p in body.paths])
    queue = request.app.state.queue
    pair_ids = moves.touched_pairs(scope, rels)
    try:
        ops.refuse_one_way(scope, rels)
        moves.check_pairs(queue, pair_ids)  # before anything moves
    except ops.FileOpError as e:
        raise _http(e) from e

    # No await between the check and the hold below, so no sync can start in between.
    async def delete_held() -> list[str]:
        with moves.hold_pairs(queue, pair_ids):
            # The pair stays held until the thread is done, even if the request is cancelled.
            kept, error, cancelled = await moves.finish_in_thread(
                ops.delete_to_recovery, request.app.state.layout, scope, rels, utcnow()
            )
        if cancelled:
            raise asyncio.CancelledError()
        if error is not None:
            raise error
        return kept

    done: list[str] = []
    try:
        kept = await guarded(delete_held())
        done = rels
    except HTTPException as e:
        if isinstance(e.detail, dict):  # stopped part-way: these were moved to Recovery
            done = e.detail.get("done", [])
        raise
    finally:
        # Also after a failure part-way: earlier items may be gone already.
        await update_listing(request, removed_from_pairs(scope, done))
        syncs = after_change(request, scope, rels)
    return OpOut(paths=kept, syncs=syncs)


def removed_from_pairs(scope: Scope, rels: list[str]) -> dict:
    out: dict[int, tuple[list[str], list]] = {}
    for rel in rels:
        pair = pair_for(scope.pairs, rel)
        if pair is not None:
            out.setdefault(pair.id, ([], []))[0].append(to_pair_rel(pair, rel))
    return out


MAX_MTIME_MS = 4_102_444_800_000  # year 2100
DISK_RESERVE_BYTES = 1024**3  # keep 1 GiB free for the database, logs and temp files


@router.post("/upload", response_model=UploadOut)
async def upload(
    request: Request,
    account_id: int,
    name: str,
    path: str = "",
    mtime_ms: int | None = None,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    """The request body is the file itself (not a form), so any size streams straight to disk.

    `name` may contain '/' for a folder upload ("Trip/day 1/photo.jpg"); folders are created.
    A file never replaces one that is already there: it gets a free name instead.
    """
    await refuse_when_disabled(request)
    scope = get_scope(request, db, account_id)
    # Adding a whole new file is safe while a sync runs (it appears atomically and the
    # follow-up sync is marked dirty), so unlike moves and deletes there is no busy check.
    # The deepest folder the file goes in (a folder upload names sub-folders in `name`); any
    # offloaded folder above it counts too. Checked before plan_target makes those folders.
    target = clean_path(path)
    if "/" in name:
        target = join_rel(target, name.rsplit("/", 1)[0])
    refuse_adding_to_offloaded(db, scope, target)
    folder, dir_rel, leaf = await run_op(plan_target, scope, clean_path(path), name)
    layout = request.app.state.layout
    tmp = new_temp(layout)
    expected = request.headers.get("content-length")
    length = int(expected) if expected is not None and expected.isdigit() else None
    if length is not None:
        free = await asyncio.to_thread(lambda: shutil.disk_usage(layout.uploads_dir).free)
        if free < length + DISK_RESERVE_BYTES:
            raise HTTPException(507, "the server doesn't have enough free disk space for this file")
    final: str | None = None
    try:
        size = await save_stream(request.stream(), tmp)
        if length is not None and size != length:
            raise HTTPException(400, "the upload was cut off; try again")
        if mtime_ms is not None and 0 < mtime_ms < MAX_MTIME_MS:
            ns = mtime_ms * 1_000_000
            await asyncio.to_thread(os.utime, tmp, ns=(ns, ns))  # keep "modified"
        # Finish the thread even if the request is cancelled: the file may already be placed.
        placed, error, cancelled = await moves.finish_in_thread(publish, tmp, folder, leaf)
        if error is None:
            final = placed
        if cancelled:
            raise asyncio.CancelledError()
        if error is not None:
            raise _http(error) from error
    except ClientDisconnect:
        raise HTTPException(400, "the upload was cancelled") from None
    except OSError as e:
        raise os_error(e) from e
    finally:
        await asyncio.shield(asyncio.to_thread(tmp.unlink, missing_ok=True))
        if final is not None:  # also after a late cancel: the file is in place
            syncs = after_change(request, scope, [join_rel(dir_rel, final)], keep_index=True)
    return UploadOut(path=join_rel(dir_rel, final), size=size, syncs=syncs)


@router.post("/new", response_model=NewFileOut)
async def new_file(
    body: NewFileIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    await refuse_when_disabled(request)
    state = request.app.state
    scope = get_scope(request, db, body.account_id)
    folder = clean_path(body.path)
    refuse_adding_to_offloaded(db, scope, folder)
    if body.kind in OFFICE_KINDS:
        templates = state.settings.office_templates_dir
        rel = await run_op(ops.new_office_file, scope, templates, folder, body.kind, body.name)
        return NewFileOut(path=rel, google_url=None, syncs=after_change(request, scope, [rel]))

    # A Google Doc/Sheet/Slides is made on Drive itself; bisync never copies those files.
    await run_op(ops.require_folder, scope, folder)  # not hidden, exists, no symlink
    pair = pair_for(scope.pairs, folder)
    if pair is None:
        raise HTTPException(
            409, "Google Docs, Sheets and Slides can only be made in a synced folder."
        )
    if scope.account.status != AccountStatus.CONNECTED:
        raise HTTPException(409, "Reconnect this Google account first (Settings, Accounts).")
    prel = to_pair_rel(pair, folder)
    catalog = await asyncio.to_thread(state.catalog.load, pair.id)
    # The ID comes from this pair's own Drive listing, so it is always inside the pair's root.
    parent_id = catalog.folder_id(prel) if catalog else None
    if parent_id is None:
        raise HTTPException(409, "This folder isn't on Google Drive yet. Sync it, then try again.")
    try:
        name = validate_name(body.name) if body.name else DEFAULT_NAMES[body.kind]
    except UnsafePath as e:
        raise HTTPException(422, str(e)) from e
    try:
        created = await state.drive_api.create_native(
            scope.account.remote_name, body.kind, name, parent_id
        )
    except DriveAuthError as e:
        await state.accounts.mark_needs_reauth(scope.account.id, str(e))
        raise HTTPException(409, "This Google account needs to be reconnected.") from e
    except DriveNotFoundError as e:
        state.catalog.request_refresh(pair.id)
        raise HTTPException(
            409, "This folder no longer exists on Google Drive; refreshing the list."
        ) from e
    except DriveForbiddenError as e:
        raise HTTPException(
            409, "You don't have permission to add files to this Google Drive folder."
        ) from e
    except DriveApiError as e:
        raise HTTPException(502, str(e)) from e
    entry = CatalogEntry(
        path=join_rel(prel, name + EXPORT_EXT[body.kind]),
        is_dir=False,
        size=0,
        mtime=utcnow(),
        id=created.id,
        mime=created.mime,
        native=body.kind,
    )
    # Show it right away; the full listing that follows confirms it.
    await asyncio.to_thread(state.catalog.store.upsert, pair.id, entry)
    state.catalog.request_refresh(pair.id)
    state.file_changes.notify(scope.account.id)
    return NewFileOut(path=None, google_url=google_url(entry), syncs={})
