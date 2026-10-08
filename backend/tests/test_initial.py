import pytest

from app.models import SyncJob
from app.services.sync.initial import CONFIRM_PHRASE, InitialSync
from tests.fake_rclone import jl


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
    fake_rclone.on("check", stdout=_check_output(), exit=1)
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
    fake_rclone.on("check", stdout=_check_output(), exit=1)
    await initial.preview(pair.id)
    job_id = await initial.run_initial(pair.id)

    backup_root = layout.backups_dir / acc.email / str(pair.id)
    copies = sorted(
        p.relative_to(backup_root).as_posix() for p in backup_root.rglob("*") if p.is_file()
    )
    assert len(copies) == 2
    assert all("/initial/" in c for c in copies)
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
