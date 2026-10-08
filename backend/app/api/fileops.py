"""Changing files from the UI. Each action changes the local copy (and, for renames and moves
of items already on Google Drive, Drive too), then queues a sync."""

import asyncio
import errno
import logging
from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.files import clean_path, get_scope
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.core.pathsafe import UnsafePath
from app.models.enums import Trigger
from app.schemas.files import DeleteIn, MkdirIn, MoveIn, OpOut, RenameIn
from app.services.files import moves, ops
from app.services.files.scope import Scope, pair_for

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
    if isinstance(e, UnsafePath):
        return HTTPException(422, str(e))
    if isinstance(e, ops.FileOpError):
        return HTTPException(e.status, str(e))
    assert isinstance(e, OSError)
    return os_error(e)


async def guarded(awaitable):
    try:
        return await awaitable
    except (UnsafePath, ops.FileOpError, OSError) as e:
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
    rels = [clean_path(p) for p in body.paths]
    kept = await run_op(ops.delete_to_recovery, request.app.state.layout, scope, rels, utcnow())
    return OpOut(paths=kept, syncs=after_change(request, scope, rels))
