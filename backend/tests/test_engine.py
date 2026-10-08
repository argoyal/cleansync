from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import as_utc, utcnow
from app.models import Account, Conflict, PlannedChange, SyncJob, SyncPair
from app.services.sync.engine import backoff
from tests.fake_rclone import jl


def _ready_pair(make_account, make_pair, **kw):
    acc = make_account()
    return acc, make_pair(acc, initialized=True, status="HEALTHY", **kw)


def _deletes(n):
    return [
        jl(f"- Path2    Queue delete                        - {{path2/}}f{i}.txt") for i in range(n)
    ]


def _jobs(db, pair_id):
    db.expire_all()
    return list(db.scalars(select(SyncJob).where(SyncJob.pair_id == pair_id).order_by(SyncJob.id)))


async def test_clean_run_records_counts_and_marks_healthy(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair)
    up = jl("- Path1    Queue copy to Path2                 - {path2/}new.txt")
    fake_rclone.on("bisync", match=["--dry-run"], stderr=[up])
    fake_rclone.on("bisync", not_match=["--dry-run"], stderr=[up])
    job_id = await sync_engine.run_pair(pair.id, "MANUAL")
    jobs = _jobs(db, pair.id)
    assert [(j.kind, j.state) for j in jobs] == [("DRY_RUN", "SUCCESS"), ("BISYNC", "SUCCESS")]
    assert jobs[1].id == job_id and jobs[1].files_uploaded == 1
    db.refresh(pair)
    assert pair.status == "HEALTHY" and pair.last_success_at is not None


async def test_bisync_runs_with_backup_dir_under_app_data(
    sync_engine, make_account, make_pair, fake_rclone, layout
):
    acc, pair = _ready_pair(make_account, make_pair)
    await sync_engine.run_pair(pair.id, "MANUAL")
    real = [c for c in fake_rclone.calls() if c[4] == "bisync" and "--dry-run" not in c][0]
    backup = real[real.index("--backup-dir1") + 1]
    assert backup.startswith(str(layout.backups_dir / "alice@example.com" / str(pair.id)))
    assert "--drive-use-trash=true" in real and "--recover" in real


async def test_too_many_deletions_block_and_nothing_changes(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, max_deletions=10)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=_deletes(11))
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    assert [c for c in fake_rclone.calls() if "--dry-run" not in c and c[4] == "bisync"] == []
    jobs = _jobs(db, pair.id)
    assert [(j.kind, j.state, j.planned_deletions) for j in jobs] == [("DRY_RUN", "BLOCKED", 11)]
    planned = db.scalars(select(PlannedChange).where(PlannedChange.job_id == jobs[0].id)).all()
    assert len(planned) == 11 and planned[0].side == "remote"
    db.refresh(pair)
    assert (pair.status, pair.attention_reason) == ("ATTENTION", "BLOCKED")
    assert "11" in pair.last_error and "10" in pair.last_error


async def test_deletions_at_the_limit_are_allowed(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, max_deletions=10)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=_deletes(10))
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    assert [j.kind for j in _jobs(db, pair.id)] == ["DRY_RUN", "BISYNC"]


async def test_override_once_applies_to_one_run_only(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, max_deletions=1, override_next=True)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=_deletes(5))
    await sync_engine.run_pair(pair.id, "MANUAL")
    await sync_engine.run_pair(pair.id, "MANUAL")
    jobs = _jobs(db, pair.id)
    assert [(j.kind, j.state) for j in jobs] == [
        ("DRY_RUN", "SUCCESS"),
        ("BISYNC", "SUCCESS"),
        ("DRY_RUN", "BLOCKED"),
    ]
    assert jobs[1].override_used is True


async def test_access_check_failure_marks_pair_error(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair)
    fake_rclone.on(
        "bisync",
        match=["--dry-run"],
        exit=2,
        stderr=[jl("Bisync critical error: check file check failed", level="error")],
    )
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    assert [c for c in fake_rclone.calls() if "--dry-run" not in c and c[4] == "bisync"] == []
    db.refresh(pair)
    assert (pair.status, pair.attention_reason) == ("ERROR", "ACCESS_CHECK")
    assert "mounted" in pair.last_error


async def test_auth_failure_marks_account_needs_reauth(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair)
    fake_rclone.on(
        "bisync",
        match=["--dry-run"],
        exit=1,
        stderr=[jl("couldn't fetch token: invalid_grant", level="error")],
    )
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    db.expire_all()
    assert db.get(Account, acc.id).status == "NEEDS_REAUTH"
    assert db.get(SyncPair, pair.id).attention_reason == "AUTH"


async def test_transient_failure_sets_backoff(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair)
    fake_rclone.on(
        "bisync",
        not_match=["--dry-run"],
        exit=5,
        stderr=[jl("connection reset by peer", level="error")],
    )
    before = utcnow()
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    db.refresh(pair)
    assert pair.fail_count == 1
    assert as_utc(pair.next_retry_at) - before >= timedelta(minutes=2) - timedelta(seconds=5)
    assert _jobs(db, pair.id)[-1].state == "FAILED"


def test_backoff_doubles_and_caps():
    assert [backoff(n).total_seconds() // 60 for n in (1, 2, 3, 6, 7, 20)] == [2, 4, 8, 60, 60, 60]


async def test_conflict_is_recorded_with_both_versions(
    sync_engine, make_account, make_pair, fake_rclone, layout, db
):
    acc, pair = _ready_pair(make_account, make_pair)
    root = layout.account_dir(acc.email)
    fake_rclone.on(
        "bisync",
        not_match=["--dry-run"],
        stderr=[jl("- WARNING  New or changed in both paths        - report.xlsx")],
        create_files=[root / "report.conflict1.xlsx", root / "report.conflict2.xlsx"],
    )
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    c = db.scalar(select(Conflict))
    assert (c.path, c.local_variant, c.remote_variant, c.state) == (
        "report.xlsx",
        "report.conflict1.xlsx",
        "report.conflict2.xlsx",
        "OPEN",
    )
    db.refresh(pair)
    assert (pair.status, pair.attention_reason) == ("ATTENTION", "CONFLICTS")


async def test_skips_paused_uninitialized_and_disconnected(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc = make_account()
    p1 = make_pair(acc, initialized=True, paused=True)
    p2 = make_pair(acc, initialized=False, local_subpath="b", remote_path="b")
    assert await sync_engine.run_pair(p1.id, "SCHEDULE") is None
    assert await sync_engine.run_pair(p2.id, "SCHEDULE") is None
    acc.status = "NEEDS_REAUTH"
    db.commit()
    p1.paused = False
    db.commit()
    assert await sync_engine.run_pair(p1.id, "SCHEDULE") is None
    assert fake_rclone.calls() == []


async def test_one_way_remote_to_local_uses_sync_with_gate(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, direction="REMOTE_TO_LOCAL", max_deletions=0)
    fake_rclone.on(
        "sync",
        match=["--dry-run"],
        stderr=[jl("Skipped delete as --dry-run is set (size 3)", level="notice", object="x.txt")],
    )
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    call = fake_rclone.calls()[0]
    assert call[4:7] == [
        "sync",
        f"{acc.remote_name}:",
        str(sync_engine.layout.account_dir(acc.email)),
    ]
    assert _jobs(db, pair.id)[0].state == "BLOCKED"


async def test_job_log_is_written_and_redacted(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair)
    fake_rclone.on("bisync", stderr=[jl("refresh 1//0gSECRETSECRETSECRET")])
    await sync_engine.run_pair(pair.id, "MANUAL")
    job = _jobs(db, pair.id)[-1]
    text = open(job.log_path).read()
    assert "refresh" in text and "SECRETSECRET" not in text


async def test_blocked_plan_keeps_real_file_names(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    # A file named like a key=value pair must not be masked in the planned changes.
    acc, pair = _ready_pair(make_account, make_pair, max_deletions=0)
    fake_rclone.on(
        "bisync",
        match=["--dry-run"],
        stderr=[jl("- Path2    Queue delete                        - {path2/}token=abc.txt")],
    )
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    job = _jobs(db, pair.id)[0]
    assert job.state == "BLOCKED"
    paths = db.scalars(select(PlannedChange.path).where(PlannedChange.job_id == job.id)).all()
    assert paths == ["token=abc.txt"]
    assert "token=abc.txt" not in open(job.log_path).read()


async def test_one_way_blocked_plan_keeps_real_file_names(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, direction="LOCAL_TO_REMOTE", max_deletions=0)
    fake_rclone.on(
        "sync",
        match=["--dry-run"],
        stderr=[
            jl(
                "Skipped delete as --dry-run is set (size 3)",
                level="notice",
                object="password=x.txt",
            )
        ],
    )
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    job = _jobs(db, pair.id)[0]
    planned = db.scalars(select(PlannedChange).where(PlannedChange.job_id == job.id)).all()
    assert [(p.side, p.action, p.path) for p in planned] == [("remote", "delete", "password=x.txt")]


async def test_runner_crash_does_not_leave_pair_running(make_account, make_pair, db, sync_engine):
    acc, pair = _ready_pair(make_account, make_pair)

    async def broken_runner(*a, **kw):
        raise OSError("rclone binary missing")

    sync_engine.runner = broken_runner
    with pytest.raises(OSError):
        await sync_engine.run_pair(pair.id, "MANUAL")
    jobs = _jobs(db, pair.id)
    assert [(j.kind, j.state) for j in jobs] == [("DRY_RUN", "FAILED")]
    assert "rclone binary missing" in jobs[0].error_message
    db.refresh(pair)
    assert (pair.status, pair.attention_reason) == ("ERROR", "FAILED")
