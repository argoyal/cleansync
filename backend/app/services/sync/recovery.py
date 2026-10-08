from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import SyncJob, SyncPair
from app.models.enums import JobState, PairStatus


def recover_interrupted(db: Session) -> list[int]:
    """Close jobs left running by a stop; return initialized pairs to sync again."""
    pair_ids: set[int] = set()
    for job in db.scalars(
        select(SyncJob).where(SyncJob.state.in_([JobState.RUNNING, JobState.QUEUED]))
    ):
        job.state = JobState.INTERRUPTED
        job.completed_at = utcnow()
        job.error_message = "DriveSync stopped while this job was running."
        pair_ids.add(job.pair_id)
    for pair in db.scalars(select(SyncPair).where(SyncPair.status == PairStatus.RUNNING)):
        pair.status = PairStatus.HEALTHY if pair.initialized else PairStatus.NEW
        pair_ids.add(pair.id)
    db.commit()
    return sorted(pid for pid in pair_ids if (p := db.get(SyncPair, pid)) and p.initialized)
