"""Sync activity (spec §3.4): Drive syncs and mail jobs in one list, newest first."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import as_utc
from app.models import Account, MailJob, SyncJob, SyncPair
from app.models.enums import JobKind
from app.schemas.mail import ActivityJobOut

router = APIRouter()


@router.get("/api/activity/jobs", response_model=list[ActivityJobOut])
def jobs(
    account_id: int | None = None,
    limit: int = 50,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 500))
    q1 = (
        select(SyncJob, SyncPair.name, SyncPair.account_id)
        .join(SyncPair, SyncPair.id == SyncJob.pair_id)
        .where(SyncJob.kind.notin_([JobKind.DRY_RUN, JobKind.PREVIEW]))
        .order_by(SyncJob.id.desc())
        .limit(limit)
    )
    q2 = (
        select(MailJob, Account.email)
        .join(Account, Account.id == MailJob.account_id)
        .order_by(MailJob.id.desc())
        .limit(limit)
    )
    if account_id is not None:
        q1 = q1.where(SyncPair.account_id == account_id)
        q2 = q2.where(MailJob.account_id == account_id)
    rows = [
        ActivityJobOut(
            source="files",
            id=j.id,
            account_id=acc_id,
            title=name,
            kind=j.kind,
            state=j.state,
            started_at=j.started_at,
            completed_at=j.completed_at,
            message=j.error_message,
            log_url=f"/api/sync-pairs/{j.pair_id}/logs/{j.id}/download",
        )
        for j, name, acc_id in db.execute(q1)
    ] + [
        ActivityJobOut(
            source="mail",
            id=j.id,
            account_id=j.account_id,
            title=f"Mail: {email}",
            kind=j.kind,
            state=j.state,
            started_at=j.started_at,
            completed_at=j.completed_at,
            message=j.message,
            log_url=f"/api/mail/jobs/{j.id}/log",
        )
        for j, email in db.execute(q2)
    ]
    rows.sort(key=lambda r: as_utc(r.started_at).timestamp() if r.started_at else 0, reverse=True)
    return rows[:limit]
