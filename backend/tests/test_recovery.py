from sqlalchemy import select

from app.models import SyncJob
from app.services.sync.recovery import recover_interrupted
from tests.test_engine import bplan


def test_startup_marks_running_jobs_interrupted_and_requeues(db, make_account, make_pair):
    acc = make_account()
    pair = make_pair(acc, initialized=True, status="RUNNING")
    db.add(SyncJob(pair_id=pair.id, kind="BISYNC", trigger="SCHEDULE", state="RUNNING"))
    db.add(SyncJob(pair_id=pair.id, kind="DRY_RUN", trigger="SCHEDULE", state="SUCCESS"))
    db.commit()
    assert recover_interrupted(db) == [pair.id]
    states = [j.state for j in db.scalars(select(SyncJob).order_by(SyncJob.id))]
    assert states == ["INTERRUPTED", "SUCCESS"]
    db.refresh(pair)
    assert pair.status == "HEALTHY"


def test_queued_jobs_get_a_not_started_message(db, make_account, make_pair):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    db.add(SyncJob(pair_id=pair.id, kind="BISYNC", trigger="SCHEDULE", state="QUEUED"))
    db.add(SyncJob(pair_id=pair.id, kind="BISYNC", trigger="SCHEDULE", state="RUNNING"))
    db.commit()
    recover_interrupted(db)
    msgs = [j.error_message for j in db.scalars(select(SyncJob).order_by(SyncJob.id))]
    assert msgs == [
        "DriveSync stopped before this job started.",
        "DriveSync stopped while this job was running.",
    ]


def test_uninitialized_pair_is_reset_but_not_requeued(db, make_account, make_pair):
    acc = make_account()
    pair = make_pair(acc, initialized=False, status="RUNNING")
    db.add(SyncJob(pair_id=pair.id, kind="PREVIEW", trigger="INITIAL", state="RUNNING"))
    db.commit()
    assert recover_interrupted(db) == []
    db.refresh(pair)
    assert pair.status == "NEW"


async def test_requeued_run_uses_recover_flag(
    sync_engine, db, make_account, make_pair, fake_rclone
):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
    await sync_engine.run_pair(pair.id, "RECOVERY")
    bisyncs = [c for c in fake_rclone.calls() if c[4] == "bisync"]
    assert len(bisyncs) == 2  # dry run + real run
    assert all("--recover" in c for c in bisyncs)


def test_app_startup_recovers_and_starts_scheduler(settings, layout, db, make_account, make_pair):
    from fastapi.testclient import TestClient

    from app.main import create_app

    acc = make_account()
    pair = make_pair(acc, initialized=True, status="RUNNING", schedule_interval_s=300)
    db.add(SyncJob(pair_id=pair.id, kind="BISYNC", trigger="SCHEDULE", state="RUNNING"))
    db.commit()
    app = create_app(settings)

    class RecordingQueue:
        def __init__(self):
            self.requests = []

        def request(self, pair_id, trigger, action="sync"):
            self.requests.append((pair_id, trigger, action))
            return "queued"

        async def stop(self):
            pass

    app.state.queue = rq = RecordingQueue()
    with TestClient(app):
        assert rq.requests == [(pair.id, "RECOVERY", "sync")]
        assert app.state.scheduler.aps.running
        assert app.state.scheduler.aps.get_job(f"pair-{pair.id}") is not None
    assert not app.state.scheduler.aps.running
    db.expire_all()
    first = db.scalars(select(SyncJob).order_by(SyncJob.id)).first()
    assert first.state == "INTERRUPTED"


def test_background_services_stay_off_when_disabled(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(settings, start_background=False)
    with TestClient(app):
        assert not app.state.scheduler.aps.running


def test_paused_pair_left_running_comes_back_paused(db, make_account, make_pair):
    acc = make_account()
    pair = make_pair(acc, initialized=True, paused=True, status="RUNNING")
    recover_interrupted(db)
    db.refresh(pair)
    assert pair.status == "PAUSED"
