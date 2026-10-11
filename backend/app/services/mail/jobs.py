"""Mail job rows (Sync activity lists them next to Drive jobs) and their log files."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import utcnow
from app.models import MailJob
from app.models.enums import JobState
from app.paths import Layout
from app.services.events.bus import EventBus

log = logging.getLogger(__name__)
SETTLED_MAIL_MSG = "The mail job stopped before it finished. It goes on at the next check."
INTERRUPTED_MAIL_MSG = (
    "CleanSync stopped while this mail job was running. It goes on at the next check."
)


def _publish(bus: EventBus, job: MailJob) -> None:
    bus.publish("mail", {"account_id": job.account_id, "job_id": job.id, "state": job.state})


def start_mail_job(
    db: Session,
    layout: Layout,
    bus: EventBus,
    account_id: int,
    kind: str,
    trigger: str,
    total: int | None = None,
) -> MailJob:
    job = MailJob(
        account_id=account_id,
        kind=kind,
        trigger=trigger,
        state=JobState.RUNNING,
        started_at=utcnow(),
        total=total,
    )
    db.add(job)
    db.commit()
    path = layout.mail_job_log(job.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")  # a fresh file per job
    job.log_path = str(path)
    db.commit()
    _publish(bus, job)
    return job


def job_log(path: str | None, line: str) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{utcnow():%Y-%m-%d %H:%M:%S} {line}\n")
    except OSError:  # a log line is never worth failing a job over
        log.warning("could not write to %s", path)


def update_mail_job(db: Session, job_id: int, *, done: int, bytes_: int) -> None:
    job = db.get(MailJob, job_id)
    if job is not None:
        job.done, job.bytes = done, bytes_
        db.commit()


def finish_mail_job(
    db: Session,
    bus: EventBus,
    job_id: int,
    state: str,
    message: str | None = None,
    *,
    done: int | None = None,
    bytes_: int | None = None,
) -> MailJob:
    job = db.get(MailJob, job_id)
    job.state = state
    job.message = message
    job.completed_at = utcnow()
    if done is not None:
        job.done = done
    if bytes_ is not None:
        job.bytes = bytes_
    db.commit()
    job_log(job.log_path, f"{state}: {message or ''}".rstrip(": "))
    _publish(bus, job)
    return job


def settle_mail_jobs(
    session_factory: sessionmaker[Session], bus: EventBus, account_id: int
) -> bool:
    """After every mail job: one still RUNNING was cut short (a crash, a cancel)."""
    with session_factory() as db:
        jobs = list(
            db.scalars(
                select(MailJob).where(
                    MailJob.account_id == account_id, MailJob.state == JobState.RUNNING
                )
            )
        )
        for job in jobs:
            finish_mail_job(db, bus, job.id, JobState.FAILED, SETTLED_MAIL_MSG)
        return bool(jobs)


def recover_mail_jobs(db: Session) -> int:
    """At startup: jobs a stop left RUNNING."""
    jobs = list(db.scalars(select(MailJob).where(MailJob.state == JobState.RUNNING)))
    for job in jobs:
        job.state = JobState.INTERRUPTED
        job.message = INTERRUPTED_MAIL_MSG
        job.completed_at = utcnow()
    db.commit()
    return len(jobs)
