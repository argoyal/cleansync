from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import as_utc, utcnow
from app.models import Account, Conflict, PlannedChange, SyncJob, SyncPair
from app.services.sync import engine as engine_mod
from app.services.sync.engine import backoff
from tests.fake_rclone import jl

ROOTS = jl('Synching Path1 "/l/" with Path2 "gd:/"')
OK = jl("Bisync successful")


def stats(deletes=0):
    return jl("\nTransferred: 0 B / 0 B", level="notice", stats={"bytes": 0, "deletes": deletes})


def bplan(*lines, deletes=0):
    """A complete, readable bisync dry-run plan: roots line, lines, success, final stats."""
    return [ROOTS, *lines, OK, stats(deletes)]


def splan(*lines, deletes=0):
    return [*lines, stats(deletes)]


def skipped_delete(path):
    return jl(
        "Skipped delete as --dry-run is set (size 3)",
        level="notice",
        skipped="delete",
        object=path,
    )


def _real_calls(fake_rclone, sub="bisync"):
    return [c for c in fake_rclone.calls() if c[4] == sub and "--dry-run" not in c]


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
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan(up))
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
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
    await sync_engine.run_pair(pair.id, "MANUAL")
    real = [c for c in fake_rclone.calls() if c[4] == "bisync" and "--dry-run" not in c][0]
    backup = real[real.index("--backup-dir1") + 1]
    assert backup.startswith(str(layout.backups_dir / "alice@example.com" / str(pair.id)))
    assert "--drive-use-trash=true" in real and "--recover" in real


async def test_too_many_deletions_block_and_nothing_changes(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, max_deletions=10)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan(*_deletes(11)))
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
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan(*_deletes(10)))
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    assert [j.kind for j in _jobs(db, pair.id)] == ["DRY_RUN", "BISYNC"]


async def test_override_once_applies_to_one_run_only(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, max_deletions=1)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan(*_deletes(5)))
    await sync_engine.run_pair(pair.id, "MANUAL")  # blocked: 5 planned
    db.refresh(pair)
    pair.override_next = True
    db.commit()
    await sync_engine.run_pair(pair.id, "MANUAL")  # same plan, override applies
    await sync_engine.run_pair(pair.id, "MANUAL")  # override used up
    jobs = _jobs(db, pair.id)
    assert [(j.kind, j.state) for j in jobs] == [
        ("DRY_RUN", "BLOCKED"),
        ("DRY_RUN", "SUCCESS"),
        ("BISYNC", "SUCCESS"),
        ("DRY_RUN", "BLOCKED"),
    ]
    assert jobs[2].override_used is True


async def test_override_is_not_applied_when_the_plan_grew(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    from app.models import Event

    acc, pair = _ready_pair(make_account, make_pair, max_deletions=1)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan(*_deletes(5)))
    await sync_engine.run_pair(pair.id, "MANUAL")
    db.refresh(pair)
    pair.override_next = True
    db.commit()
    fake_rclone.plan_file.write_text("{}")
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan(*_deletes(6)))
    await sync_engine.run_pair(pair.id, "MANUAL")
    assert _real_calls(fake_rclone) == []
    jobs = _jobs(db, pair.id)
    assert [(j.state, j.planned_deletions) for j in jobs] == [("BLOCKED", 5), ("BLOCKED", 6)]
    planned = db.scalars(select(PlannedChange).where(PlannedChange.job_id == jobs[1].id)).all()
    assert len(planned) == 6
    db.refresh(pair)
    assert pair.override_next is False
    msgs = db.scalars(select(Event.message)).all()
    assert any("override not applied: plan changed from 5 to 6 deletions" in m for m in msgs)


async def test_override_without_an_earlier_blocked_plan_is_not_applied(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, max_deletions=1, override_next=True)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan(*_deletes(5)))
    await sync_engine.run_pair(pair.id, "MANUAL")
    assert [j.state for j in _jobs(db, pair.id)] == ["BLOCKED"]


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
    assert pair.last_error == (
        "Marker file RCLONE_TEST is missing on one side. Is the storage mounted? "
        "Nothing was changed."
    )


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
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
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
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
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
        stderr=splan(skipped_delete("x.txt")),
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
    fake_rclone.on("bisync", stderr=bplan(jl("refresh 1//0gSECRETSECRETSECRET")))
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
        stderr=bplan(jl("- Path2    Queue delete                        - {path2/}token=abc.txt")),
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
        stderr=splan(skipped_delete("password=x.txt")),
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


@pytest.mark.parametrize(
    "dry_lines",
    [
        [jl("something rclone never said before")],  # nothing recognisable
        [jl("Bisync successful"), stats()],  # no roots line
        [ROOTS, stats()],  # no "Bisync successful"
        [ROOTS, OK],  # no final stats
    ],
)
async def test_unreadable_bisync_plan_fails_closed(
    sync_engine, make_account, make_pair, fake_rclone, db, dry_lines
):
    acc, pair = _ready_pair(make_account, make_pair)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=dry_lines)
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    assert _real_calls(fake_rclone) == []
    jobs = _jobs(db, pair.id)
    assert [(j.kind, j.state) for j in jobs] == [("DRY_RUN", "FAILED")]
    assert jobs[0].error_message == "Couldn't read rclone's plan; nothing was changed"
    db.refresh(pair)
    assert (pair.status, pair.attention_reason) == ("ATTENTION", "PLAN_UNREADABLE")


async def test_unreadable_one_way_plan_fails_closed(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, direction="LOCAL_TO_REMOTE")
    fake_rclone.on("sync", match=["--dry-run"], stderr=[skipped_delete("a.txt")])  # no stats
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    assert _real_calls(fake_rclone, "sync") == []
    assert [j.state for j in _jobs(db, pair.id)] == ["FAILED"]


async def test_gate_uses_final_stats_when_they_show_more_deletes(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, max_deletions=10)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan(*_deletes(2), deletes=40))
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    assert _real_calls(fake_rclone) == []
    jobs = _jobs(db, pair.id)
    assert [(j.state, j.planned_deletions) for j in jobs] == [("BLOCKED", 40)]


async def test_one_way_counts_structured_skipped_deletes_only(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, direction="LOCAL_TO_REMOTE", max_deletions=0)
    # A delete line rclone words differently is still counted from its "skipped" field.
    reworded = jl("Not deleting (dry run)", level="notice", skipped="delete", object="b.txt")
    copy = jl("Skipped copy as --dry-run is set", level="notice", skipped="copy", object="c")
    fake_rclone.on("sync", match=["--dry-run"], stderr=splan(reworded, copy))
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    jobs = _jobs(db, pair.id)
    assert [(j.state, j.planned_deletions) for j in jobs] == [("BLOCKED", 1)]


async def test_one_way_real_run_is_capped_at_the_limit(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, direction="LOCAL_TO_REMOTE", max_deletions=7)
    fake_rclone.on("sync", match=["--dry-run"], stderr=splan(skipped_delete("a.txt")))
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    real = _real_calls(fake_rclone, "sync")[0]
    assert real[real.index("--max-delete") + 1] == "7"
    assert [j.state for j in _jobs(db, pair.id)] == ["SUCCESS", "SUCCESS"]


async def test_one_way_override_caps_the_real_run_at_the_approved_count(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, direction="LOCAL_TO_REMOTE", max_deletions=1)
    dels = [skipped_delete(f"f{i}.txt") for i in range(3)]
    fake_rclone.on("sync", match=["--dry-run"], stderr=splan(*dels))
    await sync_engine.run_pair(pair.id, "MANUAL")
    db.refresh(pair)
    pair.override_next = True
    db.commit()
    await sync_engine.run_pair(pair.id, "MANUAL")
    real = _real_calls(fake_rclone, "sync")[0]
    assert real[real.index("--max-delete") + 1] == "3"


async def test_bisync_safety_brake_blocks_without_retry(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair)
    fake_rclone.on(
        "bisync",
        match=["--dry-run"],
        exit=1,
        stderr=[
            jl(
                'too many deletes (>50%, 5 of 7) on Path1 "/l/". Run with --force if desired.',
                level="error",
            ),
            jl("Failed to bisync: too many deletes", level="notice"),
        ],
    )
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    db.refresh(pair)
    assert (pair.status, pair.attention_reason) == ("ATTENTION", "BLOCKED")
    assert "50%" in pair.last_error
    assert pair.fail_count == 0 and pair.next_retry_at is None
    assert _real_calls(fake_rclone) == []


async def test_conflict_uses_rclone_variant_names_and_counts_new_rows(
    sync_engine, make_account, make_pair, fake_rclone, layout, db
):
    acc, pair = _ready_pair(make_account, make_pair)
    root = layout.account_dir(acc.email)
    for n in (1, 2):  # an older, still open conflict on the same file
        (root / f"report.conflict{n}.xlsx").write_text("old")
    db.add(
        Conflict(
            pair_id=pair.id,
            path="report.xlsx",
            local_variant="report.conflict1.xlsx",
            remote_variant="report.conflict2.xlsx",
        )
    )
    db.commit()
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
    fake_rclone.on(
        "bisync",
        not_match=["--dry-run"],
        stderr=[
            jl("- WARNING  New or changed in both paths        - report.xlsx"),
            jl("- Path1    Renaming Path1 copy                - {path1/}report.conflict3.xlsx"),
            jl("- Path2    Renaming Path2 copy                - {path2/}report.conflict4.xlsx"),
        ],
        create_files=[root / "report.conflict3.xlsx", root / "report.conflict4.xlsx"],
    )
    job_id = await sync_engine.run_pair(pair.id, "SCHEDULE")
    rows = db.scalars(select(Conflict).order_by(Conflict.id)).all()
    assert [(c.local_variant, c.remote_variant) for c in rows] == [
        ("report.conflict1.xlsx", "report.conflict2.xlsx"),
        ("report.conflict3.xlsx", "report.conflict4.xlsx"),
    ]
    assert db.get(SyncJob, job_id).conflicts == 1


async def test_conflicts_are_recorded_even_when_the_run_fails(
    sync_engine, make_account, make_pair, fake_rclone, layout, db
):
    acc, pair = _ready_pair(make_account, make_pair)
    root = layout.account_dir(acc.email)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
    fake_rclone.on(
        "bisync",
        not_match=["--dry-run"],
        exit=5,
        stderr=[
            jl("- WARNING  New or changed in both paths        - a.txt"),
            jl("- WARNING  New or changed in both paths        - ghost.txt"),
            jl("connection reset by peer", level="error"),
        ],
        create_files=[root / "a.conflict1.txt", root / "a.conflict2.txt"],
    )
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    rows = db.scalars(select(Conflict)).all()
    assert [(c.path, c.local_variant) for c in rows] == [("a.txt", "a.conflict1.txt")]
    assert _jobs(db, pair.id)[-1].state == "FAILED"


@pytest.mark.parametrize("reason", ["BLOCKED", "ACCESS_CHECK", "NEEDS_RESYNC"])
async def test_schedule_and_watcher_do_not_retry_pairs_waiting_for_the_user(
    sync_engine, make_account, make_pair, fake_rclone, db, reason
):
    acc, pair = _ready_pair(make_account, make_pair)
    pair.status, pair.attention_reason = "ATTENTION", reason
    db.commit()
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
    assert await sync_engine.run_pair(pair.id, "SCHEDULE") is None
    assert await sync_engine.run_pair(pair.id, "WATCHER") is None
    assert await sync_engine.run_pair(pair.id, "RECOVERY") is None
    assert fake_rclone.calls() == []
    assert await sync_engine.run_pair(pair.id, "MANUAL") is not None


async def test_capped_planned_rows_keep_deletes_first(
    sync_engine, make_account, make_pair, fake_rclone, db, monkeypatch
):
    monkeypatch.setattr(engine_mod, "_MAX_PLANNED_ROWS", 2)
    acc, pair = _ready_pair(make_account, make_pair, max_deletions=0)
    copies = [jl(f"- Path1    Queue copy to Path2    - {{path2/}}c{i}.txt") for i in range(3)]
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan(*copies, *_deletes(2)))
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    job = _jobs(db, pair.id)[0]
    planned = db.scalars(select(PlannedChange).where(PlannedChange.job_id == job.id)).all()
    assert [p.action for p in planned] == ["delete", "delete"]


# ---- final review fixes -------------------------------------------------------------------


def _transient():
    return [jl("connection reset by peer", level="error")]


async def test_override_is_used_up_even_when_the_dry_run_fails(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    # C1: an approval must not survive a failed attempt and apply to a later, unreviewed plan.
    acc, pair = _ready_pair(make_account, make_pair, max_deletions=10)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan(*_deletes(20)))
    await sync_engine.run_pair(pair.id, "MANUAL")  # blocked: 20 planned
    db.refresh(pair)
    pair.override_next = True
    db.commit()
    fake_rclone.plan_file.write_text("{}")
    fake_rclone.on("bisync", match=["--dry-run"], exit=5, stderr=_transient())
    await sync_engine.run_pair(pair.id, "MANUAL")  # the approved run fails before the gate
    db.refresh(pair)
    assert pair.override_next is False
    pair.next_retry_at = None  # the retry wait has passed
    db.commit()
    fake_rclone.plan_file.write_text("{}")
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan(*_deletes(15)))
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    assert _real_calls(fake_rclone) == []
    assert _jobs(db, pair.id)[-1].state == "BLOCKED"


def _max_delete(call):
    return call[call.index("--max-delete") + 1]


async def test_bisync_dry_run_lets_drivesync_count_and_real_run_keeps_the_brake(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    # I1: bisync's own 50% brake must not stop the dry run, or nothing is stored to review.
    acc, pair = _ready_pair(make_account, make_pair)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    calls = [c for c in fake_rclone.calls() if c[4] == "bisync"]
    assert [_max_delete(c) for c in calls] == ["100", "50"]


async def test_override_lifts_bisyncs_own_brake_for_that_run(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, max_deletions=1)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan(*_deletes(5)))
    await sync_engine.run_pair(pair.id, "MANUAL")
    db.refresh(pair)
    pair.override_next = True
    db.commit()
    await sync_engine.run_pair(pair.id, "MANUAL")
    assert _max_delete(_real_calls(fake_rclone)[0]) == "100"


async def test_one_way_max_delete_stop_explains_the_count_cap(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair, direction="LOCAL_TO_REMOTE", max_deletions=3)
    fake_rclone.on("sync", match=["--dry-run"], stderr=splan(skipped_delete("a.txt")))
    fake_rclone.on(
        "sync",
        not_match=["--dry-run"],
        exit=7,
        stderr=[
            jl("Fatal error received - not attempting retries", level="error"),
            jl("--max-delete threshold reached", level="error"),
        ],
    )
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    db.refresh(pair)
    assert (pair.status, pair.attention_reason) == ("ATTENTION", "BLOCKED")
    assert "50%" not in pair.last_error
    assert "3" in pair.last_error


@pytest.mark.parametrize("trigger", ["SCHEDULE", "WATCHER", "RECOVERY"])
async def test_automatic_triggers_wait_for_the_retry_backoff(
    sync_engine, make_account, make_pair, fake_rclone, db, trigger
):
    # I8: the watcher (and recovery) must not bypass the retry wait the scheduler honours.
    acc, pair = _ready_pair(make_account, make_pair)
    pair.next_retry_at = utcnow() + timedelta(minutes=5)
    pair.attention_reason = "FAILED"
    db.commit()
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
    assert await sync_engine.run_pair(pair.id, trigger) is None
    assert fake_rclone.calls() == []


async def test_manual_sync_ignores_the_retry_backoff(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair)
    pair.next_retry_at = utcnow() + timedelta(minutes=5)
    db.commit()
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
    assert await sync_engine.run_pair(pair.id, "MANUAL") is not None


async def test_automatic_trigger_runs_once_the_backoff_has_passed(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    acc, pair = _ready_pair(make_account, make_pair)
    pair.next_retry_at = utcnow() - timedelta(seconds=1)
    db.commit()
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
    assert await sync_engine.run_pair(pair.id, "WATCHER") is not None


async def test_pausing_during_a_run_keeps_the_pair_paused(
    sync_engine, make_account, make_pair, fake_rclone, db, session_factory
):
    # M2: the engine must not overwrite PAUSED with HEALTHY when the run finishes.
    acc, pair = _ready_pair(make_account, make_pair)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
    real_runner = sync_engine.runner

    async def pausing_runner(args, *a, **kw):
        if "--dry-run" not in args:
            with session_factory() as other:
                p = other.get(SyncPair, pair.id)
                p.paused, p.status = True, "PAUSED"
                other.commit()
        return await real_runner(args, *a, **kw)

    sync_engine.runner = pausing_runner
    await sync_engine.run_pair(pair.id, "MANUAL")
    db.expire_all()
    p = db.get(SyncPair, pair.id)
    assert (p.status, p.paused) == ("PAUSED", True)
    assert _jobs(db, pair.id)[-1].state == "SUCCESS"


async def test_pausing_during_a_failing_run_keeps_paused_and_the_reason(
    sync_engine, make_account, make_pair, fake_rclone, db, session_factory
):
    acc, pair = _ready_pair(make_account, make_pair)
    fake_rclone.on("bisync", match=["--dry-run"], stderr=bplan())
    fake_rclone.on("bisync", not_match=["--dry-run"], exit=5, stderr=_transient())
    real_runner = sync_engine.runner

    async def pausing_runner(args, *a, **kw):
        if "--dry-run" not in args:
            with session_factory() as other:
                p = other.get(SyncPair, pair.id)
                p.paused, p.status = True, "PAUSED"
                other.commit()
        return await real_runner(args, *a, **kw)

    sync_engine.runner = pausing_runner
    await sync_engine.run_pair(pair.id, "MANUAL")
    db.expire_all()
    p = db.get(SyncPair, pair.id)
    assert (p.status, p.attention_reason) == ("PAUSED", "FAILED")


async def test_one_way_backup_moves_count_as_planned_deletes(
    sync_engine, make_account, make_pair, fake_rclone, db
):
    # I5: with a backup dir rclone words a dry-run delete as "move into backup dir".
    acc, pair = _ready_pair(
        make_account, make_pair, direction="REMOTE_TO_LOCAL", max_deletions=0, backups_enabled=True
    )
    moved = jl(
        "Skipped move into backup dir as --dry-run is set (size 2)",
        level="notice",
        skipped="move into backup dir",
        object="old.txt",
    )
    fake_rclone.on("sync", match=["--dry-run"], stderr=splan(moved, deletes=0))
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    job = _jobs(db, pair.id)[0]
    assert (job.state, job.planned_deletions) == ("BLOCKED", 1)
    planned = db.scalars(select(PlannedChange).where(PlannedChange.job_id == job.id)).all()
    assert [(p.side, p.action, p.path) for p in planned] == [("local", "delete", "old.txt")]
