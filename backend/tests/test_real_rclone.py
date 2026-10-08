import os
import shutil
import tempfile
from pathlib import Path

import pytest

from app.services.rclone import commands as c
from app.services.rclone.parse import Counters, count_dry_run_deletes, parse_check_combined
from app.services.rclone.runner import run_rclone
from app.services.sync.initial import CONFIRM_PHRASE

pytestmark = [
    pytest.mark.realrclone,
    pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed"),
]


@pytest.fixture
def tmp_path():
    # Local-to-local bisync names its listing files after the full path of both sides, so long
    # pytest temp paths hit "file name too long". Real runs use short remote names.
    d = Path(tempfile.mkdtemp(prefix="r"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _setup(tmp_path: Path):
    p1, p2 = tmp_path / "p1", tmp_path / "p2"
    for p in (p1, p2):
        p.mkdir()
        (p / "RCLONE_TEST").write_text("x")
        (p / "keep.txt").write_text("same")
        (p / "both.txt").write_text("base")
    env = c.RcloneEnv(
        binary="rclone",
        config_file=tmp_path / "rclone.conf",
        cache_dir=tmp_path / "cache",
        config_pass="",
    )
    (tmp_path / "rclone.conf").write_text("")
    filters = tmp_path / "filters.txt"
    filters.write_text("- /.google-exports/**\n")
    t = c.BisyncTarget(
        local=p1, remote=str(p2), workdir=tmp_path / "work", filters_file=filters, backup_dir=None
    )
    return env, t, p1, p2


async def _run(env, t, tmp_path, **kw):
    counters = Counters()
    res = await run_rclone(
        c.bisync_args(env, t, **kw), env.env(), log_path=tmp_path / "log.txt", on_line=counters.feed
    )
    return res, counters


async def test_real_bisync_delete_is_counted(tmp_path):
    env, t, p1, p2 = _setup(tmp_path)
    res, _ = await _run(env, t, tmp_path, dry_run=False, resync=True)
    assert res.exit_code == 0, (tmp_path / "log.txt").read_text()
    (p1 / "keep.txt").unlink()
    res, counters = await _run(env, t, tmp_path, dry_run=True)
    assert res.exit_code == 0, (tmp_path / "log.txt").read_text()
    assert counters.deleted_remote == 1, (tmp_path / "log.txt").read_text()
    assert ("remote", "delete", "keep.txt") in counters.planned


async def test_real_bisync_conflict_is_detected_and_keeps_both(tmp_path):
    env, t, p1, p2 = _setup(tmp_path)
    await _run(env, t, tmp_path, dry_run=False, resync=True)
    (p1 / "both.txt").write_text("local edit, longer")
    (p2 / "both.txt").write_text("remote edit")
    res, counters = await _run(env, t, tmp_path, dry_run=False)
    assert res.exit_code == 0, res.error_tail
    assert counters.conflict_paths == ["both.txt"], (tmp_path / "log.txt").read_text()
    names = sorted(p.name for p in p1.iterdir())
    assert "both.conflict1.txt" in names and "both.conflict2.txt" in names, names
    # DriveSync's conflict screen relies on conflict1 = local (Path1), conflict2 = Drive (Path2).
    assert (p1 / "both.conflict1.txt").read_text() == "local edit, longer"
    assert (p1 / "both.conflict2.txt").read_text() == "remote edit"


async def test_real_second_conflict_points_at_conflict3_and_4(
    tmp_path, db, make_account, make_pair
):
    from sqlalchemy import select

    from app.models import Conflict
    from app.services.sync.conflicts import record_conflicts

    env, t, p1, p2 = _setup(tmp_path)
    pair = make_pair(make_account())
    await _run(env, t, tmp_path, dry_run=False, resync=True)
    for n in (1, 2):
        (p1 / "both.txt").write_text(f"local edit {n}, longer")
        (p2 / "both.txt").write_text(f"remote {n}")
        res, counters = await _run(env, t, tmp_path, dry_run=False)
        assert res.exit_code == 0, res.error_tail
        assert (
            record_conflicts(db, pair, p1, counters.conflict_paths, counters.conflict_variants) == 1
        ), counters.conflict_variants
    rows = db.scalars(select(Conflict).order_by(Conflict.id)).all()
    assert [(c.path, c.local_variant, c.remote_variant) for c in rows] == [
        ("both.txt", "both.conflict1.txt", "both.conflict2.txt"),
        ("both.txt", "both.conflict3.txt", "both.conflict4.txt"),
    ]
    assert (p1 / "both.conflict3.txt").read_text() == "local edit 2, longer"
    assert (p1 / "both.conflict4.txt").read_text() == "remote 2"


async def test_real_dry_run_plan_is_readable(tmp_path):
    env, t, p1, p2 = _setup(tmp_path)
    await _run(env, t, tmp_path, dry_run=False, resync=True)
    (p1 / "keep.txt").unlink()
    res, counters = await _run(env, t, tmp_path, dry_run=True)
    assert res.exit_code == 0
    assert counters.has_roots and counters.bisync_successful
    assert counters.final_stats is not None and counters.final_stats["deletes"] == 1


async def test_real_bisync_safety_brake_is_classified(tmp_path):
    from app.services.rclone.errors import Failure, classify_failure

    env, t, p1, p2 = _setup(tmp_path)
    for i in range(6):
        (p1 / f"f{i}.txt").write_text("x")
        (p2 / f"f{i}.txt").write_text("x")
    await _run(env, t, tmp_path, dry_run=False, resync=True)
    for i in range(6):
        (p1 / f"f{i}.txt").unlink()
    res, _ = await _run(env, t, tmp_path, dry_run=True)
    assert classify_failure(res.exit_code, res.error_tail) == Failure.TOO_MANY_DELETES


async def test_real_one_way_sync_max_delete_stops_extra_deletes(tmp_path):
    from app.services.rclone.errors import Failure, classify_failure

    env, t, p1, p2 = _setup(tmp_path)
    for i in range(4):
        (p2 / f"x{i}.txt").write_text("x")
    res = await run_rclone(
        c.sync_args(env, str(p1), str(p2), t.filters_file, None, dry_run=False, max_delete=1),
        env.env(),
        log_path=None,
    )
    assert classify_failure(res.exit_code, res.error_tail) == Failure.TOO_MANY_DELETES
    assert len(list(p2.glob("x*.txt"))) == 3


async def test_real_access_check_blocks_when_marker_missing(tmp_path):
    env, t, p1, p2 = _setup(tmp_path)
    await _run(env, t, tmp_path, dry_run=False, resync=True)
    (p1 / "RCLONE_TEST").unlink()
    res, _ = await _run(env, t, tmp_path, dry_run=False)
    assert res.exit_code != 0
    assert (p2 / "keep.txt").exists()


def _filtered_dirs(tmp_path: Path):
    env, t, p1, p2 = _setup(tmp_path)
    t.filters_file.write_text("- /skip/**\n+ **\n")
    (p1 / "skip").mkdir()
    (p1 / "skip" / "a.txt").write_text("a")
    (p2 / "skip").mkdir()
    (p2 / "skip" / "b.txt").write_text("b")
    (p1 / "new.txt").write_text("n")
    (p2 / "extra.txt").write_text("e")
    return env, t, p1, p2


async def test_real_sync_dry_run_honours_filter_from(tmp_path):
    env, t, p1, p2 = _filtered_dirs(tmp_path)
    lines = []
    res = await run_rclone(
        c.sync_args(env, str(p1), str(p2), t.filters_file, None, dry_run=True),
        env.env(),
        log_path=None,
        on_line=lines.append,
    )
    assert res.exit_code == 0, res.error_tail
    skipped = [ln for ln in lines if ln.skipped]
    objs = {ln.obj for ln in skipped}
    assert {"new.txt", "extra.txt"} <= objs, [ln.raw for ln in lines]
    assert not any(o.startswith("skip/") for o in objs), objs
    assert sum(count_dry_run_deletes(ln) for ln in lines) == 1
    assert not (p2 / "new.txt").exists()


async def test_real_check_honours_filter_from_and_parses_combined(tmp_path):
    env, t, p1, p2 = _filtered_dirs(tmp_path)
    out = []
    res = await run_rclone(
        c.check_args(env, p1, str(p2), t.filters_file),
        env.env(),
        log_path=None,
        on_stdout=out.append,
    )
    assert res.exit_code == 1, res.error_tail  # differences found
    parsed = {m for m in map(parse_check_combined, out) if m}
    assert ("+", "new.txt") in parsed and ("-", "extra.txt") in parsed
    assert ("=", "keep.txt") in parsed
    assert not any(path.startswith("skip/") for _, path in parsed)


from app.services.rclone.setup import ensure_config_encrypted


async def test_real_config_gets_encrypted(tmp_path):
    passcmd = tmp_path / "print-pass"
    passcmd.write_text('#!/bin/sh\nprintf "%s" "$RCLONE_CONFIG_PASS"\n')
    passcmd.chmod(0o755)
    conf = tmp_path / "rclone.conf"
    env = c.RcloneEnv(
        binary="rclone",
        config_file=conf,
        cache_dir=tmp_path / "cache",
        config_pass="correct horse",
        password_command=str(passcmd),
    )
    res = await run_rclone(
        env.base() + ["config", "create", "t", "local", "--non-interactive"],
        env.env(),
        log_path=None,
    )
    assert res.exit_code == 0
    await ensure_config_encrypted(env)
    assert conf.read_text().startswith("# Encrypted rclone configuration File")
    out: list[str] = []
    res = await run_rclone(
        env.base() + ["listremotes"], env.env(), log_path=None, on_stdout=out.append
    )
    assert res.exit_code == 0 and "t:" in out


async def test_real_first_sync_merges_without_deleting_and_backs_up():
    """Whole InitialSync flow on real rclone; Drive is an rclone `alias` remote to a local dir."""
    from sqlalchemy.orm import sessionmaker

    from app.database.engine import make_engine
    from app.database.migrate import run_migrations
    from app.models import Account, SyncPair
    from app.paths import Layout
    from app.services.events.bus import EventBus
    from app.services.sync.engine import SyncEngine
    from app.services.sync.initial import InitialSync

    root = Path(tempfile.mkdtemp(prefix="r"))
    try:
        layout = Layout(root)
        layout.ensure()
        run_migrations(layout.db_file)
        eng = make_engine(layout.db_file)
        sf = sessionmaker(eng, expire_on_commit=False)
        drive = root / "d"
        drive.mkdir()
        env = c.RcloneEnv(
            binary="rclone",
            config_file=layout.rclone_conf,
            cache_dir=layout.rclone_cache,
            config_pass="",
        )
        layout.rclone_conf.write_text("")
        r = await run_rclone(
            [*env.base(), "config", "create", "gd_t", "alias", "remote", str(drive)],
            env.env(),
            log_path=None,
        )
        assert r.exit_code == 0, r.error_tail
        with sf() as db:
            acc = Account(email="alice@example.com", remote_name="gd_t")
            db.add(acc)
            db.commit()
            pair = SyncPair(account_id=acc.id, name="Docs", local_subpath="", remote_path="")
            db.add(pair)
            db.commit()
            pair_id = pair.id
        local = layout.account_dir("alice@example.com")
        local.mkdir(parents=True, exist_ok=True)
        (local / "same.txt").write_text("same")
        (drive / "same.txt").write_text("same")
        (local / "only-local.txt").write_text("L")
        (drive / "only-drive.txt").write_text("D")
        (local / "differ.txt").write_text("local version")
        (drive / "differ.txt").write_text("drive version, longer")
        os.utime(drive / "differ.txt", (1, 1))  # drive copy is older, local wins

        engine = SyncEngine(
            layout=layout, session_factory=sf, bus=EventBus(), rclone_env=env, accounts=None
        )
        initial = InitialSync(engine)
        await initial.preview(pair_id)
        p = initial.load_preview(pair_id)
        assert p is not None, "preview failed"
        assert (p["same"], p["only_local"], p["only_remote"], p["different"]) == (1, 1, 1, 1)
        assert p["samples"]["different"] == ["differ.txt"]

        initial.check_phrase(CONFIRM_PHRASE)
        await initial.run_initial(pair_id)
        with sf() as db:
            pair = db.get(SyncPair, pair_id)
            assert pair.initialized is True, pair.last_error
        for side in (local, drive):
            assert (side / "only-local.txt").read_text() == "L"
            assert (side / "only-drive.txt").read_text() == "D"
            assert (side / "same.txt").read_text() == "same"
            assert (side / "differ.txt").exists()
        backups = list((layout.backups_dir / "alice@example.com" / str(pair_id)).rglob("*.txt"))
        assert [b.name for b in backups] == ["differ.txt"]
        assert "/initial/" in backups[0].as_posix()
        assert backups[0].read_text() == "local version"
        eng.dispose()
    finally:
        shutil.rmtree(root, ignore_errors=True)
