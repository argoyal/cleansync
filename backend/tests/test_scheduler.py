from datetime import timedelta

from app.core.clock import utcnow
from app.services.scheduler import Scheduler


class StubQueue:
    def __init__(self):
        self.requests = []

    def request(self, pair_id, trigger, action="sync"):
        self.requests.append((pair_id, trigger, action))
        return "queued"


def _sched(session_factory, layout, queue):
    return Scheduler(session_factory=session_factory, queue=queue, accounts=None, layout=layout)


async def test_reload_registers_interval_jobs_for_eligible_pairs(
    session_factory, layout, make_account, make_pair
):
    acc = make_account()
    ok = make_pair(acc, initialized=True, schedule_interval_s=900)
    make_pair(
        acc,
        initialized=True,
        paused=True,
        schedule_interval_s=900,
        local_subpath="p",
        remote_path="p",
    )
    make_pair(acc, initialized=True, schedule_interval_s=None, local_subpath="m", remote_path="m")
    make_pair(acc, initialized=False, schedule_interval_s=900, local_subpath="n", remote_path="n")
    s = _sched(session_factory, layout, StubQueue())
    s.start()
    try:
        ids = sorted(j.id for j in s.aps.get_jobs() if j.id.startswith("pair-"))
        assert ids == [f"pair-{ok.id}"]
        assert s.aps.get_job(f"pair-{ok.id}").trigger.interval == timedelta(seconds=900)
    finally:
        s.shutdown()


async def test_reload_drops_jobs_for_pairs_no_longer_eligible(
    session_factory, layout, make_account, make_pair, db
):
    acc = make_account()
    pair = make_pair(acc, initialized=True, schedule_interval_s=300)
    s = _sched(session_factory, layout, StubQueue())
    s.start()
    try:
        assert s.aps.get_job(f"pair-{pair.id}") is not None
        pair.paused = True
        db.commit()
        s.reload()
        assert s.aps.get_job(f"pair-{pair.id}") is None
    finally:
        s.shutdown()


async def test_tick_requests_sync(session_factory, layout, make_account, make_pair):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    q = StubQueue()
    await _sched(session_factory, layout, q).tick(pair.id)
    assert q.requests == [(pair.id, "SCHEDULE", "sync")]


async def test_tick_respects_backoff(session_factory, layout, make_account, make_pair, db):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    pair.next_retry_at = utcnow() + timedelta(minutes=5)
    db.commit()
    q = StubQueue()
    await _sched(session_factory, layout, q).tick(pair.id)
    assert q.requests == []


async def test_tick_skips_account_needing_reauth(
    session_factory, layout, make_account, make_pair, db
):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    acc.status = "NEEDS_REAUTH"
    db.commit()
    q = StubQueue()
    s = _sched(session_factory, layout, q)
    await s.tick(pair.id)
    assert q.requests == []


async def test_daily_maintenance_backs_up_db_and_prunes(
    session_factory, layout, make_account, make_pair
):
    acc = make_account()
    pair = make_pair(acc, backup_retention_days=7)
    old = layout.backups_dir / acc.email / str(pair.id) / "2000-01-01"
    old.mkdir(parents=True)
    _sched(session_factory, layout, StubQueue()).daily_maintenance()
    assert not old.exists()
    assert list(layout.db_backups.glob("drivesync-*.db"))


async def test_daily_and_quota_jobs_coalesce_and_tolerate_late_starts(session_factory, layout):
    s = _sched(session_factory, layout, StubQueue())
    s.start()
    try:
        for job_id in ("daily", "quotas"):
            job = s.aps.get_job(job_id)
            assert job.coalesce is True and job.misfire_grace_time == 3600
    finally:
        s.shutdown()


async def test_daily_maintenance_prunes_logs_and_isolates_failures(
    session_factory, layout, make_account, make_pair, monkeypatch
):
    import os
    import time

    old = layout.job_log(1)
    old.write_text("x")
    long_ago = time.time() - 90 * 86400
    os.utime(old, (long_ago, long_ago))
    acc = make_account()
    pair = make_pair(acc, backup_retention_days=7)
    oldb = layout.backups_dir / acc.email / str(pair.id) / "2000-01-01"
    oldb.mkdir(parents=True)

    def boom(*a, **k):
        raise RuntimeError("prune broke")

    monkeypatch.setattr("app.services.scheduler.prune_backups", boom)
    _sched(session_factory, layout, StubQueue()).daily_maintenance()
    assert not old.exists()  # log pruning still ran after the backup prune failed
