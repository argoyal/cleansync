"""Changing files from the UI. Each action changes the local copy (and, for renames and moves
of items already on Google Drive, Drive too), then queues a sync."""

import asyncio
import errno
import logging
import os
from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from app.api.files import clean_path, get_scope
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.core.pathsafe import UnsafePath
from app.models.enums import Trigger
from app.schemas.files import DeleteIn, MkdirIn, MoveIn, OpOut, RenameIn, UploadOut
from app.services.files import moves, ops
from app.services.files.scope import Scope, join_rel, pair_for
from app.services.files.upload import new_temp, plan_target, publish, save_stream

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files")


def refuse_when_disabled(request: Request) -> None:
    # With syncing switched off nothing would carry the change to Drive: refuse up front.
    if getattr(request.app.state.queue, "disabled_reason", None) is not None:
        raise HTTPException(503, moves.DISABLED_MSG)


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
    request: Request, scope: Scope, rels: list[str], refresh: Iterable[int] = ()
) -> dict[int, str]:
    """Drop cached statuses, queue a sync for every touched pair, tell open tabs."""
    state = request.app.state
    syncs: dict[int, str] = {}
    for pair_id in refresh:  # moved on Drive: list it again so the browser shows it there
        state.catalog.request_refresh(pair_id)
    for rel in rels:
        pair = pair_for(scope.pairs, rel)
        if pair is None or pair.id in syncs:
            continue
        state.indexes.invalidate(pair.id)
        syncs[pair.id] = (
            state.queue.request(pair.id, Trigger.APP) if pair.initialized else "not_initialized"
        )
    state.bus.publish("files", {"account_id": scope.account.id})
    return syncs


async def _move(request: Request, scope: Scope, steps: list[ops.MoveStep]) -> OpOut:
    state = request.app.state
    plan = await guarded(
        moves.prepare_moves(
            scope, steps, queue=state.queue, catalog=state.catalog, drive=state.drive_mover
        )
    )
    try:
        moved = await guarded(moves.run_moves(plan, queue=state.queue, drive=state.drive_mover))
    finally:
        # Also after a failure part-way: earlier items may have moved already.
        syncs = after_change(request, scope, plan.touched, plan.drive_pairs)
    return OpOut(paths=moved, syncs=syncs)


@router.post("/mkdir", response_model=OpOut)
async def mkdir(
    body: MkdirIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    refuse_when_disabled(request)
    scope = get_scope(request, db, body.account_id)
    rel = await run_op(ops.make_folder, scope, clean_path(body.path), body.name)
    return OpOut(paths=[rel], syncs=after_change(request, scope, [rel]))


@router.post("/rename", response_model=OpOut)
async def rename(
    body: RenameIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    refuse_when_disabled(request)
    scope = get_scope(request, db, body.account_id)
    step = await run_op(ops.plan_rename, scope, clean_path(body.path), body.new_name)
    return await _move(request, scope, [step])


@router.post("/move", response_model=OpOut)
async def move(
    body: MoveIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    refuse_when_disabled(request)
    scope = get_scope(request, db, body.account_id)
    sources = [clean_path(p) for p in body.paths]
    steps = await run_op(ops.plan_move, scope, sources, clean_path(body.to))
    return await _move(request, scope, steps)


@router.post("/delete", response_model=OpOut)
async def delete(
    body: DeleteIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    refuse_when_disabled(request)
    scope = get_scope(request, db, body.account_id)
    rels = ops.prune_selection([clean_path(p) for p in body.paths])
    queue = request.app.state.queue
    pair_ids = moves.touched_pairs(scope, rels)
    try:
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

    try:
        kept = await guarded(delete_held())
    finally:
        # Also after a failure part-way: earlier items may be gone already.
        syncs = after_change(request, scope, rels)
    return OpOut(paths=kept, syncs=syncs)


MAX_MTIME_MS = 4_102_444_800_000  # year 2100


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
    refuse_when_disabled(request)
    scope = get_scope(request, db, account_id)
    queue = request.app.state.queue

    def check(dir_rel: str) -> None:
        moves.check_pairs(queue, moves.touched_pairs(scope, [dir_rel]))

    folder, dir_rel, leaf = await run_op(
        lambda: plan_target(scope, clean_path(path), name, before_create=check)
    )
    pair_ids = moves.touched_pairs(scope, [dir_rel])
    tmp = new_temp(request.app.state.layout)
    expected = request.headers.get("content-length")

    async def publish_held() -> str:
        moves.check_pairs(queue, pair_ids)  # it may have become busy during the upload
        with moves.hold_pairs(queue, pair_ids):
            # Held until the thread is done, even if the request is cancelled.
            final, error, cancelled = await moves.finish_in_thread(publish, tmp, folder, leaf)
        if cancelled:
            raise asyncio.CancelledError()
        if error is not None:
            raise error
        return final

    try:
        size = await save_stream(request.stream(), tmp)
        if expected is not None and expected.isdigit() and size != int(expected):
            raise HTTPException(400, "the upload was cut off; try again")
        if mtime_ms is not None and 0 < mtime_ms < MAX_MTIME_MS:
            os.utime(tmp, ns=(mtime_ms * 1_000_000, mtime_ms * 1_000_000))  # keep "modified"
        final = await guarded(publish_held())
    except ClientDisconnect:
        raise HTTPException(400, "the upload was cancelled") from None
    except OSError as e:
        raise os_error(e) from e
    finally:
        tmp.unlink(missing_ok=True)
    rel = join_rel(dir_rel, final)
    return UploadOut(path=rel, size=size, syncs=after_change(request, scope, [rel]))
