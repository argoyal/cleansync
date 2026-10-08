import pytest

from app.models import SyncJob
from app.services.sync.initial import CONFIRM_PHRASE, InitialSync
from tests.fake_rclone import check_out, jl


@pytest.fixture
def initial(sync_engine):
    return InitialSync(sync_engine)


def _check_output():
    return [
        "= same.txt",
        "+ only-local.txt",
        "- only-drive.pdf",
        "* both/differ.xlsx",
        "* other.docx",
    ]


async def test_preview_counts_and_samples(initial, make_account, make_pair, fake_rclone):
    acc = make_account()
    pair = make_pair(acc)
    fake_rclone.on("check", **check_out(_check_output()))
    await initial.preview(pair.id)
    p = initial.load_preview(pair.id)
    assert (p["same"], p["only_local"], p["only_remote"], p["different"], p["errors"]) == (
        1,
        1,
        1,
        2,
        0,
    )
    assert p["samples"]["different"] == ["both/differ.xlsx", "other.docx"]
    call = fake_rclone.calls()[0]
    assert call[4] == "check" and "--combined" in call


async def test_preview_failure_is_reported(initial, make_account, make_pair, fake_rclone, db):
    acc = make_account()
    pair = make_pair(acc)
    fake_rclone.on("check", exit=3, stderr=[jl("directory not found", level="error")])
    job_id = await initial.preview(pair.id)
    assert db.get(SyncJob, job_id).state == "FAILED"
    assert initial.load_preview(pair.id) is None


def test_phrase_must_match_exactly(initial):
    initial.check_phrase(CONFIRM_PHRASE)
    for bad in [
        "",
        "i understand and want to start synchronization.",
        "I understand and want to start synchronization",
    ]:
        with pytest.raises(ValueError):
            initial.check_phrase(bad)


async def test_initial_requires_preview(initial, make_account, make_pair):
    acc = make_account()
    pair = make_pair(acc)
    with pytest.raises(ValueError, match="preview"):
        await initial.run_initial(pair.id)


async def test_initial_backs_up_markers_and_resyncs(
    initial, make_account, make_pair, fake_rclone, layout, db
):
    acc = make_account()
    pair = make_pair(acc)
    root = layout.account_dir(acc.email)
    (root / "both").mkdir()
    (root / "both" / "differ.xlsx").write_text("local version")
    (root / "other.docx").write_text("local other")
    fake_rclone.on("check", **check_out(_check_output()))
    await initial.preview(pair.id)
    job_id = await initial.run_initial(pair.id)

    backup_root = layout.backups_dir / acc.email / str(pair.id)
    copies = sorted(
        p.relative_to(backup_root).as_posix() for p in backup_root.rglob("*") if p.is_file()
    )
    assert len(copies) == 2
    assert all("/.drivesync-initial-" in c for c in copies)
    assert (root / "RCLONE_TEST").exists()
    calls = fake_rclone.calls()
    touch = [c for c in calls if c[4] == "touch"][0]
    assert touch[5] == f"{acc.remote_name}:RCLONE_TEST"
    bisync = [c for c in calls if c[4] == "bisync"][0]
    assert "--resync" in bisync and "newer" in bisync and "--dry-run" not in bisync
    db.refresh(pair)
    assert pair.initialized is True and pair.status == "HEALTHY"
    assert db.get(SyncJob, job_id).kind == "RESYNC"


async def test_failed_resync_leaves_pair_uninitialized(
    initial, make_account, make_pair, fake_rclone, db
):
    acc = make_account()
    pair = make_pair(acc)
    fake_rclone.on("check", stdout=["= a"], exit=0)
    fake_rclone.on("bisync", exit=2, stderr=[jl("boom", level="error")])
    await initial.preview(pair.id)
    await initial.run_initial(pair.id)
    db.refresh(pair)
    assert pair.initialized is False


async def test_engine_runs_safe_resync_when_filters_changed(
    initial, sync_engine, make_account, make_pair, fake_rclone, db
):
    acc = make_account()
    pair = make_pair(acc, initialized=True, needs_resync=True)
    fake_rclone.on("check", stdout=["= a"], exit=0)
    await sync_engine.run_pair(pair.id, "SCHEDULE")
    bisyncs = [c for c in fake_rclone.calls() if c[4] == "bisync"]
    assert len(bisyncs) == 1 and "--resync" in bisyncs[0]
    db.refresh(pair)
    assert pair.needs_resync is False


async def test_safe_resync_never_has_dry_run_gate_bypass(
    initial, make_account, make_pair, fake_rclone
):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    fake_rclone.on("check", stdout=["= a"], exit=0)
    await initial.safe_resync(pair.id, "MANUAL")
    bisync = [c for c in fake_rclone.calls() if c[4] == "bisync"][0]
    assert "--resync-mode" in bisync and "--drive-use-trash=true" in bisync


async def test_safe_resync_stops_when_check_fails(
    initial, make_account, make_pair, fake_rclone, db
):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    fake_rclone.on("check", exit=3, stderr=[jl("directory not found", level="error")])
    await initial.safe_resync(pair.id, "MANUAL")
    assert not [c for c in fake_rclone.calls() if c[4] == "bisync"]


def _backups(layout, acc, pair):
    root = layout.backups_dir / acc.email / str(pair.id)
    return (
        {p.relative_to(root).as_posix().split("/", 1)[1]: p for p in root.rglob("*") if p.is_file()}
        if root.exists()
        else {}
    )


async def test_initial_uses_a_fresh_check_not_the_stale_preview(
    initial, make_account, make_pair, fake_rclone, layout
):
    acc = make_account()
    pair = make_pair(acc)
    root = layout.account_dir(acc.email)
    (root / "late.txt").write_text("changed after preview")
    fake_rclone.on("check", stdout=["= a"], exit=0)
    await initial.preview(pair.id)
    fake_rclone.plan_file.write_text("{}")
    fake_rclone.on("check", **check_out(["* late.txt"]))
    await initial.run_initial(pair.id)
    copies = _backups(layout, acc, pair)
    assert [k.split("/", 1)[1] for k in copies] == ["late.txt"]
    assert initial.load_preview(pair.id) is None
    assert not (layout.previews_dir / f"{pair.id}-different.txt").exists()


async def test_backup_folders_are_unique_per_job(
    initial, make_account, make_pair, fake_rclone, layout
):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    (layout.account_dir(acc.email) / "x.txt").write_text("v1")
    fake_rclone.on("check", **check_out(["* x.txt"]))
    await initial.safe_resync(pair.id, "MANUAL")
    (layout.account_dir(acc.email) / "x.txt").write_text("v2")
    await initial.safe_resync(pair.id, "MANUAL")
    texts = sorted(p.read_text() for p in _backups(layout, acc, pair).values())
    assert texts == ["v1", "v2"]


async def test_backup_failure_fails_job_and_skips_resync(
    initial, make_account, make_pair, fake_rclone, layout, db, monkeypatch
):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    (layout.account_dir(acc.email) / "x.txt").write_text("v1")
    fake_rclone.on("check", **check_out(["* x.txt"]))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(InitialSync, "_copy_files", staticmethod(boom))
    job_id = await initial.safe_resync(pair.id, "MANUAL")
    job = db.get(SyncJob, job_id)
    db.refresh(job)
    assert job.state == "FAILED" and "disk full" in job.error_message
    assert not [c for c in fake_rclone.calls() if c[4] in ("bisync", "touch")]


async def test_error_paths_are_backed_up_but_resync_is_refused(
    initial, make_account, make_pair, fake_rclone, layout, db
):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    (layout.account_dir(acc.email) / "bad.txt").write_text("v")
    fake_rclone.on("check", **check_out(["! bad.txt", "! other.txt"]))
    job_id = await initial.safe_resync(pair.id, "MANUAL")
    job = db.get(SyncJob, job_id)
    db.refresh(job)
    assert job.state == "FAILED"
    assert job.error_message == "rclone could not compare 2 files; nothing was changed"
    db.refresh(pair)
    assert pair.status == "ATTENTION"
    assert not [c for c in fake_rclone.calls() if c[4] == "bisync"]


async def test_error_paths_are_in_the_backup_list(initial, make_account, make_pair, fake_rclone):
    acc = make_account()
    pair = make_pair(acc)
    fake_rclone.on("check", **check_out(["! bad.txt", "* d.txt"]))
    await initial.preview(pair.id)
    f = initial._different_file(pair.id)
    assert f.read_text().splitlines() == ["bad.txt", "d.txt"]


async def test_case_clash_local_only_file_is_backed_up(
    initial, make_account, make_pair, fake_rclone, layout
):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    (layout.account_dir(acc.email) / "Report.TXT").write_text("local")
    (layout.account_dir(acc.email) / "plain.txt").write_text("p")
    fake_rclone.on("check", **check_out(["+ Report.TXT", "- report.txt", "+ plain.txt"]))
    await initial.safe_resync(pair.id, "MANUAL")
    assert [k.split("/", 1)[1] for k in _backups(layout, acc, pair)] == ["Report.TXT"]


async def test_one_way_pair_is_not_resynced(
    initial, sync_engine, make_account, make_pair, fake_rclone, db
):
    acc = make_account()
    pair = make_pair(acc, direction="REMOTE_TO_LOCAL", initialized=True, needs_resync=True)
    with pytest.raises(ValueError):
        await initial.safe_resync(pair.id, "MANUAL")
    fake_rclone.on("sync", stderr=[], exit=0)
    await sync_engine.run_pair(pair.id, "MANUAL")
    assert not [c for c in fake_rclone.calls() if c[4] == "bisync"]
    db.refresh(pair)
    assert pair.needs_resync is False


async def test_initial_for_one_way_pair_marks_initialized_without_bisync(
    initial, make_account, make_pair, fake_rclone, db
):
    acc = make_account()
    pair = make_pair(acc, direction="LOCAL_TO_REMOTE")
    fake_rclone.on("check", stdout=["= a"], exit=0)
    await initial.preview(pair.id)
    await initial.run_initial(pair.id)
    assert not [c for c in fake_rclone.calls() if c[4] in ("bisync", "touch")]
    db.refresh(pair)
    assert pair.initialized is True and pair.status == "HEALTHY"


async def test_failed_initial_does_not_back_off_and_says_rerun_preview(
    initial, make_account, make_pair, fake_rclone, db
):
    acc = make_account()
    pair = make_pair(acc)
    fake_rclone.on("check", stdout=["= a"], exit=0)
    fake_rclone.on("bisync", exit=2, stderr=[jl("boom", level="error")])
    await initial.preview(pair.id)
    await initial.run_initial(pair.id)
    db.refresh(pair)
    assert pair.fail_count == 0 and pair.next_retry_at is None
    assert "preview" in pair.last_error


# I4: exit 1 from `rclone check` only means "differences found" when the output says exactly that.
@pytest.mark.parametrize(
    "output",
    [
        # the remote could not even be opened
        {
            "stdout": [],
            "stderr": [
                jl(
                    'Failed to create file system for "gd_x:": didn\'t find section in config',
                    level="critical",
                )
            ],
            "exit": 1,
        },
        # no closing summary at all
        {"stdout": ["+ a.txt"], "stderr": [], "exit": 1},
        # the summary counts more differences than the listing showed
        {
            "stdout": ["+ a.txt"],
            "stderr": [jl("5 differences found", level="notice")],
            "exit": 1,
        },
        # errors the listing does not explain (e.g. a folder that could not be read)
        check_out(["+ a.txt"], extra_errors=1),
    ],
)
async def test_check_exit_1_that_is_not_just_differences_fails(
    initial, make_account, make_pair, fake_rclone, db, output
):
    acc = make_account()
    pair = make_pair(acc)
    fake_rclone.on("check", **output)
    job_id = await initial.preview(pair.id)
    assert db.get(SyncJob, job_id).state == "FAILED"
    assert initial.load_preview(pair.id) is None
    db.refresh(pair)
    assert pair.status == "ERROR" and pair.last_error


async def test_initial_refuses_to_resync_when_the_fresh_check_is_unexplained(
    initial, make_account, make_pair, fake_rclone, db
):
    acc = make_account()
    pair = make_pair(acc)
    fake_rclone.on("check", **check_out(["+ a.txt"]))
    await initial.preview(pair.id)
    fake_rclone.plan_file.write_text("{}")
    fake_rclone.on("check", **check_out(["+ a.txt"], extra_errors=2))
    await initial.run_initial(pair.id)
    assert [c for c in fake_rclone.calls() if c[4] == "bisync"] == []
    db.refresh(pair)
    assert pair.initialized is False
