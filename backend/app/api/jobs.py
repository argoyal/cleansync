from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.pairs import load_pair
from app.auth.deps import AuthContext, get_db, require_user
from app.models import Account, PlannedChange, SyncJob, SyncPair
from app.models.enums import AccountStatus, JobKind, JobState, PairStatus
from app.schemas.jobs import JobOut
from app.services.logs import tail_lines

router = APIRouter()


@router.get("/api/status")
def status(ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    def count(model, *where) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    return {
        "accounts": count(Account, Account.status != AccountStatus.DISCONNECTED),
        "accounts_attention": count(Account, Account.status == AccountStatus.NEEDS_REAUTH),
        "pairs": count(SyncPair),
        "new": count(SyncPair, SyncPair.status == PairStatus.NEW),
        "healthy": count(SyncPair, SyncPair.status == PairStatus.HEALTHY),
        "attention": count(SyncPair, SyncPair.status.in_([PairStatus.ATTENTION, PairStatus.ERROR])),
        "running": count(SyncPair, SyncPair.status == PairStatus.RUNNING),
        "paused": count(SyncPair, SyncPair.status == PairStatus.PAUSED),
    }


@router.get("/api/sync-pairs/{pair_id}/history", response_model=list[JobOut])
def history(
    pair_id: int,
    limit: int = 50,
    include_dry_runs: bool = False,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    load_pair(db, pair_id)
    q = (
        select(SyncJob)
        .where(SyncJob.pair_id == pair_id)
        .order_by(SyncJob.id.desc())
        .limit(max(1, min(limit, 500)))
    )
    if not include_dry_runs:
        q = q.where(SyncJob.kind.notin_([JobKind.DRY_RUN, JobKind.PREVIEW]))
    return [JobOut.model_validate(j, from_attributes=True) for j in db.scalars(q)]


def _job_for_pair(db: Session, pair_id: int, job_id: int | None) -> SyncJob:
    if job_id is None:
        job = db.scalar(
            select(SyncJob).where(SyncJob.pair_id == pair_id).order_by(SyncJob.id.desc())
        )
    else:
        job = db.get(SyncJob, job_id)
    if job is None or job.pair_id != pair_id:
        raise HTTPException(404, "job not found")
    return job


@router.get("/api/sync-pairs/{pair_id}/logs")
def logs(
    pair_id: int,
    request: Request,
    job_id: int | None = None,
    tail: int = 500,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    load_pair(db, pair_id)
    if job_id is None and not db.scalar(select(SyncJob.id).where(SyncJob.pair_id == pair_id)):
        return {"job_id": None, "lines": []}
    job = _job_for_pair(db, pair_id, job_id)
    path = request.app.state.layout.job_log(job.id)
    return {"job_id": job.id, "lines": tail_lines(path, max(1, min(tail, 5000)))}


@router.get("/api/sync-pairs/{pair_id}/logs/{job_id}/download")
def download_log(
    pair_id: int,
    job_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    load_pair(db, pair_id)
    job = _job_for_pair(db, pair_id, job_id)
    path = request.app.state.layout.job_log(job.id)
    if not path.exists():
        raise HTTPException(404, "log file not found")
    return FileResponse(
        path, media_type="text/plain", filename=f"drivesync-pair{pair_id}-job{job.id}.log"
    )


@router.get("/api/sync-pairs/{pair_id}/planned-changes")
def planned_changes(
    pair_id: int,
    job_id: int | None = None,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    load_pair(db, pair_id)
    if job_id is None:
        job = db.scalar(
            select(SyncJob)
            .where(SyncJob.pair_id == pair_id, SyncJob.state == JobState.BLOCKED)
            .order_by(SyncJob.id.desc())
        )
        if job is None:
            return {"job_id": None, "changes": []}
    else:
        job = _job_for_pair(db, pair_id, job_id)
    rows = db.scalars(
        select(PlannedChange).where(PlannedChange.job_id == job.id).order_by(PlannedChange.id)
    )
    return {
        "job_id": job.id,
        "changes": [{"side": r.side, "action": r.action, "path": r.path} for r in rows],
    }
