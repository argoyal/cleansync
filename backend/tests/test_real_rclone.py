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
    # The engine's dry run lifts the brake (100%) so DriveSync can count and store the plan.
    res, counters = await _run(env, t, tmp_path, dry_run=True, max_delete_percent=100)
    assert res.exit_code == 0, res.error_tail
    assert counters.bisync_successful and counters.deleted_remote == 6
    # The real run keeps bisync's 50% brake.
    res, _ = await _run(env, t, tmp_path, dry_run=False)
    assert classify_failure(res.exit_code, res.error_tail) == Failure.TOO_MANY_DELETES
    assert (p2 / "f0.txt").exists()
    # "Override once" on that stored plan reruns it with the brake lifted, and it goes through.
    res, counters = await _run(env, t, tmp_path, dry_run=False, max_delete_percent=100)
    assert res.exit_code == 0, res.error_tail
    assert counters.deleted_remote == 6
    assert not any((p2 / f"f{i}.txt").exists() for i in range(6))


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
    eng = None
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
        (local / "drivenew.txt").write_text("old local")
        (drive / "drivenew.txt").write_text("drive newer, longer")
        os.utime(local / "drivenew.txt", (1, 1))  # local copy is older, Drive wins

        engine = SyncEngine(
            layout=layout, session_factory=sf, bus=EventBus(), rclone_env=env, accounts=None
        )
        initial = InitialSync(engine)
        await initial.preview(pair_id)
        p = initial.load_preview(pair_id)
        assert p is not None, "preview failed"
        assert (p["same"], p["only_local"], p["only_remote"], p["different"]) == (1, 1, 1, 2)
        assert sorted(p["samples"]["different"]) == ["differ.txt", "drivenew.txt"]

        initial.check_phrase(CONFIRM_PHRASE)
        await initial.run_initial(pair_id)
        with sf() as db:
            pair = db.get(SyncPair, pair_id)
            assert pair.initialized is True, pair.last_error
        for side in (local, drive):
            assert (side / "only-local.txt").read_text() == "L"
            assert (side / "only-drive.txt").read_text() == "D"
            assert (side / "same.txt").read_text() == "same"
            assert (side / "differ.txt").read_text() == "local version"
            assert (side / "drivenew.txt").read_text() == "drive newer, longer"
        backups = {
            b.name: b.read_text()
            for b in (layout.backups_dir / "alice@example.com" / str(pair_id)).rglob("*.txt")
        }
        assert backups == {"differ.txt": "local version", "drivenew.txt": "old local"}
        assert not initial.load_preview(pair_id)
    finally:
        if eng is not None:
            eng.dispose()
        shutil.rmtree(root, ignore_errors=True)


async def test_real_one_way_dry_run_with_backup_dir_counts_deletes(tmp_path):
    # Drive -> local with backups: rclone says "move into backup dir", not "delete".
    env, t, p1, p2 = _setup(tmp_path)
    (p1 / "gone.txt").write_text("only local")
    lines = []
    res = await run_rclone(
        c.sync_args(env, str(p2), str(p1), t.filters_file, tmp_path / "bk", dry_run=True),
        env.env(),
        log_path=None,
        on_line=lines.append,
    )
    assert res.exit_code == 0, res.error_tail
    deletes = [ln.obj for ln in lines if count_dry_run_deletes(ln)]
    assert deletes == ["gone.txt"], [ln.raw for ln in lines]
    assert (p1 / "gone.txt").exists()


async def _real_check(env, local, remote, filters):
    from app.services.rclone.parse import CheckSummary

    out: list[str] = []
    summary = CheckSummary()
    res = await run_rclone(
        c.check_args(env, local, remote, filters),
        env.env(),
        log_path=None,
        on_line=summary.feed,
        on_stdout=out.append,
    )
    parsed = [m for m in map(parse_check_combined, out) if m]
    diffs = sum(1 for k, _ in parsed if k in "+-*")
    bangs = sum(1 for k, _ in parsed if k == "!")
    return res, summary.explains(differences=diffs, errors=bangs)


async def test_real_check_differences_are_explained_by_the_summary(tmp_path):
    env, t, p1, p2 = _filtered_dirs(tmp_path)
    res, explained = await _real_check(env, p1, str(p2), t.filters_file)
    assert res.exit_code == 1 and explained


async def test_real_check_with_a_missing_remote_is_not_just_differences(tmp_path):
    # Exit 1 too, but nothing was compared: the remote isn't in the config.
    env, t, p1, p2 = _setup(tmp_path)
    res, explained = await _real_check(env, p1, "gd_missing:Docs", t.filters_file)
    assert res.exit_code == 1 and not explained


async def test_real_check_with_a_new_remote_folder_lists_everything_as_local_only(tmp_path):
    # A Drive folder that doesn't exist yet is an empty side, not an error.
    env, t, p1, p2 = _setup(tmp_path)
    res, explained = await _real_check(env, p1, str(tmp_path / "not-there"), t.filters_file)
    assert res.exit_code == 1 and explained


async def test_real_initial_preview_fails_for_a_missing_remote(tmp_path):
    from sqlalchemy.orm import sessionmaker

    from app.database.engine import make_engine
    from app.database.migrate import run_migrations
    from app.models import Account, SyncJob, SyncPair
    from app.paths import Layout
    from app.services.events.bus import EventBus
    from app.services.sync.engine import SyncEngine
    from app.services.sync.initial import InitialSync

    class _Accounts:
        async def mark_needs_reauth(self, *a):
            pass

    layout = Layout(tmp_path / "root")
    layout.ensure()
    run_migrations(layout.db_file)
    eng = make_engine(layout.db_file)
    sf = sessionmaker(eng, expire_on_commit=False)
    env = c.RcloneEnv(
        binary="rclone",
        config_file=layout.rclone_conf,
        cache_dir=layout.rclone_cache,
        config_pass="",
    )
    layout.rclone_conf.write_text("")
    with sf() as db:
        acc = Account(email="alice@example.com", remote_name="gd_missing")
        db.add(acc)
        db.commit()
        layout.account_dir(acc.email).mkdir(parents=True, exist_ok=True)
        pair = SyncPair(account_id=acc.id, name="Docs", local_subpath="", remote_path="Docs")
        db.add(pair)
        db.commit()
        pair_id = pair.id
    engine = SyncEngine(
        layout=layout, session_factory=sf, bus=EventBus(), rclone_env=env, accounts=_Accounts()
    )
    initial = InitialSync(engine)
    try:
        job_id = await initial.preview(pair_id)
        with sf() as db:
            assert db.get(SyncJob, job_id).state == "FAILED"
        assert initial.load_preview(pair_id) is None
    finally:
        eng.dispose()


async def test_real_engine_brake_then_override_once(tmp_path):
    # The whole R2 path through the engine with real rclone: a plan under the user's limit trips
    # bisync's 50% brake on the real run, is stored for review, and "Override once" runs it.
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from app.database.engine import make_engine
    from app.database.migrate import run_migrations
    from app.models import Account, PlannedChange, SyncJob, SyncPair
    from app.paths import Layout
    from app.services.events.bus import EventBus
    from app.services.sync.engine import SyncEngine
    from app.services.sync.filters import refresh_pair_filters

    class _Accounts:
        async def mark_needs_reauth(self, *a):
            pass

    layout = Layout(tmp_path / "root")
    layout.ensure()
    run_migrations(layout.db_file)
    eng = make_engine(layout.db_file)
    sf = sessionmaker(eng, expire_on_commit=False)
    env = c.RcloneEnv(
        binary="rclone",
        config_file=layout.rclone_conf,
        cache_dir=layout.rclone_cache,
        config_pass="",
    )
    layout.rclone_conf.write_text("[gd_alice]\ntype = local\n")  # stands in for Google Drive
    remote_dir = tmp_path / "drive"
    engine = SyncEngine(
        layout=layout, session_factory=sf, bus=EventBus(), rclone_env=env, accounts=_Accounts()
    )
    try:
        with sf() as db:
            acc = Account(email="alice@example.com", remote_name="gd_alice")
            db.add(acc)
            db.commit()
            pair = SyncPair(
                account_id=acc.id,
                name="Docs",
                local_subpath="",
                remote_path=str(remote_dir),
                initialized=True,
                status="HEALTHY",
                max_deletions=10,
                backups_enabled=False,
            )
            db.add(pair)
            db.commit()
            refresh_pair_filters(db, layout, pair)
            local_dir = layout.account_dir(acc.email)
            for d in (local_dir, remote_dir):
                d.mkdir(parents=True, exist_ok=True)
                (d / "RCLONE_TEST").write_text("x")
                for i in range(8):
                    (d / f"f{i}.txt").write_text("x")
            seed = await run_rclone(
                c.bisync_args(env, engine.bisync_target(acc, pair), dry_run=False, resync=True),
                env.env(),
                log_path=None,
            )
            assert seed.exit_code == 0, seed.error_tail
            for i in range(6):  # 6 of 9 files: under the limit of 10, over bisync's 50%
                (local_dir / f"f{i}.txt").unlink()
            pair_id = pair.id

        await engine.run_pair(pair_id, "SCHEDULE")
        with sf() as db:
            pair = db.get(SyncPair, pair_id)
            jobs = db.scalars(select(SyncJob).order_by(SyncJob.id)).all()
            assert [(j.kind, j.state) for j in jobs] == [
                ("DRY_RUN", "BLOCKED"),
                ("BISYNC", "FAILED"),
            ]
            planned = db.scalars(
                select(PlannedChange).where(PlannedChange.job_id == jobs[0].id)
            ).all()
            assert sorted(p.path for p in planned if p.action == "delete") == [
                f"f{i}.txt" for i in range(6)
            ]
            assert pair.attention_reason == "BLOCKED" and "50%" in pair.last_error
            assert (remote_dir / "f0.txt").exists()
            pair.override_next = True
            db.commit()

        await engine.run_pair(pair_id, "MANUAL")
        with sf() as db:
            pair = db.get(SyncPair, pair_id)
            last = db.scalars(select(SyncJob).order_by(SyncJob.id.desc())).first()
            assert (last.kind, last.state, last.override_used) == ("BISYNC", "SUCCESS", True), (
                last.error_message
            )
            assert pair.status == "HEALTHY"
        assert not any((remote_dir / f"f{i}.txt").exists() for i in range(6))
        assert (remote_dir / "f7.txt").exists()
    finally:
        eng.dispose()


async def test_real_lsjson_metadata_listing_parses_into_a_catalog(tmp_path):
    import json as _json

    from app.services.catalog.model import parse_item

    root = tmp_path / "drive"
    (root / "Work" / "Deep").mkdir(parents=True)
    (root / "Work" / "a b [1].txt").write_text("hello")
    (root / "top.pdf").write_bytes(b"%PDF-1.4")
    conf = tmp_path / "rclone.conf"
    conf.write_text(f"[al]\ntype = alias\nremote = {root}\n")
    env = c.RcloneEnv(
        binary="rclone", config_file=conf, cache_dir=tmp_path / "cache", config_pass=""
    )
    lines: list[str] = []
    res = await run_rclone(
        c.lsjson_args(
            env, "al:", recursive=True, fast_list=True, metadata=True, show_all_gdocs=True
        ),
        env.env(),
        log_path=None,
        on_stdout=lines.append,
    )
    assert res.exit_code == 0, res.error_tail
    # One item per line: the catalog parses the listing line by line, never as one blob.
    items = [_json.loads(ln.rstrip(",")) for ln in lines if ln.startswith("{")]
    entries = {e.path: e for e in map(parse_item, items)}
    assert set(entries) == {"Work", "Work/Deep", "Work/a b [1].txt", "top.pdf"}
    assert entries["Work"].is_dir and entries["Work/a b [1].txt"].size == 5
    assert entries["top.pdf"].mime == "application/pdf"
    assert all(e.id is None and e.native is None for e in entries.values())

    out: list[str] = []
    res = await run_rclone(
        c.lsjson_args(env, "al:Work", stat=True), env.env(), log_path=None, on_stdout=out.append
    )
    assert res.exit_code == 0 and _json.loads("\n".join(out))["IsDir"] is True

    res = await run_rclone(c.lsjson_args(env, "al:Nope", recursive=True), env.env(), log_path=None)
    assert res.exit_code != 0
    assert any("directory not found" in ln.lower() for ln in res.error_tail), res.error_tail


async def test_real_config_dump_reads_an_encrypted_config(tmp_path):
    import json as _json

    passcmd = tmp_path / "pass.sh"
    passcmd.write_text('#!/bin/sh\nprintf "%s" "$RCLONE_CONFIG_PASS"\n')
    passcmd.chmod(0o755)
    env = c.RcloneEnv(
        binary="rclone",
        config_file=tmp_path / "rclone.conf",
        cache_dir=tmp_path / "cache",
        config_pass="a-long-test-passphrase",
        password_command=str(passcmd),
    )
    res = await run_rclone(
        c.config_create_alias_args(env, "local_1", tmp_path), env.env(), log_path=None
    )
    assert res.exit_code == 0, res.error_tail
    from app.services.rclone.setup import ensure_config_encrypted

    assert await ensure_config_encrypted(env) is True
    out: list[str] = []
    res = await run_rclone(c.config_dump_args(env), env.env(), log_path=None, on_stdout=out.append)
    assert res.exit_code == 0, res.error_tail
    assert _json.loads("\n".join(out))["local_1"]["type"] == "alias"


@pytest.mark.parametrize("others", [6, 0])  # 0: the renamed folder is most of the pair
async def test_real_rename_on_both_sides_leaves_the_next_sync_nothing_to_do(tmp_path, others):
    """A folder renamed through DriveSync moves on Drive and on disk; bisync then sees the same
    change on both sides: nothing to copy, nothing to delete, no conflicts. Even when the folder
    holds more than half the pair's files (bisync's 50% brake counts the old paths)."""
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from app.database.engine import make_engine
    from app.database.migrate import run_migrations
    from app.models import Account, Conflict, SyncJob, SyncPair
    from app.paths import Layout
    from app.services.catalog.service import CatalogService
    from app.services.events.bus import EventBus
    from app.services.files import ops
    from app.services.files.moves import DriveMover, prepare_moves, run_moves
    from app.services.files.scope import load_scope
    from app.services.queue import SyncQueue
    from app.services.sync.engine import SyncEngine
    from app.services.sync.filters import refresh_pair_filters

    class _Accounts:
        async def mark_needs_reauth(self, *a):
            raise AssertionError("no login problem expected")

    layout = Layout(tmp_path / "root")
    layout.ensure()
    run_migrations(layout.db_file)
    eng = make_engine(layout.db_file)
    sf = sessionmaker(eng, expire_on_commit=False)
    env = c.RcloneEnv(
        binary="rclone",
        config_file=layout.rclone_conf,
        cache_dir=layout.rclone_cache,
        config_pass="",
    )
    layout.rclone_conf.write_text("[gd_alice]\ntype = local\n")  # stands in for Google Drive
    remote_dir = tmp_path / "drive"
    bus = EventBus()
    engine = SyncEngine(
        layout=layout, session_factory=sf, bus=bus, rclone_env=env, accounts=_Accounts()
    )
    catalog = CatalogService(
        layout=layout, session_factory=sf, bus=bus, rclone_env=env, accounts=_Accounts()
    )
    drive = DriveMover(layout=layout, env=env, accounts=_Accounts())

    async def never(*a):
        raise AssertionError("the queue must not run anything here")

    queue = SyncQueue(never)
    try:
        with sf() as db:
            acc = Account(email="alice@example.com", remote_name="gd_alice")
            db.add(acc)
            db.commit()
            pair = SyncPair(
                account_id=acc.id,
                name="Docs",
                local_subpath="",
                remote_path=str(remote_dir),
                initialized=True,
                status="HEALTHY",
                max_deletions=10,
            )
            db.add(pair)
            db.commit()
            refresh_pair_filters(db, layout, pair)
            local_dir = layout.account_dir(acc.email)
            for d in (local_dir, remote_dir):
                (d / "Old" / "sub").mkdir(parents=True, exist_ok=True)
                (d / "RCLONE_TEST").write_text("x")
                (d / "Old" / "a.txt").write_text("alpha")
                (d / "Old" / "sub" / "b.txt").write_text("beta")
                for i in range(others):
                    (d / f"f{i}.txt").write_text("x")
            seed = await run_rclone(
                c.bisync_args(env, engine.bisync_target(acc, pair), dry_run=False, resync=True),
                env.env(),
                log_path=None,
            )
            assert seed.exit_code == 0, seed.error_tail
            pair_id = pair.id
        listing = await catalog.refresh(pair_id)
        assert listing is not None and "Old/sub/b.txt" in listing.files

        with sf() as db:
            scope = load_scope(db, layout, acc.id)
        step = ops.plan_rename(scope, "Old", "New")
        plan = await prepare_moves(scope, [step], queue=queue, catalog=catalog, drive=drive)
        assert plan.drive_pairs == {pair_id}
        assert await run_moves(plan, queue=queue, drive=drive) == ["New"]
        for side in (local_dir, remote_dir):
            assert not (side / "Old").exists()
            assert (side / "New" / "a.txt").read_text() == "alpha"
            assert (side / "New" / "sub" / "b.txt").read_text() == "beta"

        # A plain dry run (bisync's 50% brake lifted, as the engine's dry run does).
        with sf() as db:
            target = engine.bisync_target(db.get(Account, acc.id), db.get(SyncPair, pair_id))
        counters = Counters()
        res = await run_rclone(
            c.bisync_args(env, target, dry_run=True, max_delete_percent=100),
            env.env(),
            log_path=tmp_path / "dry.log",
            on_line=counters.feed,
        )
        assert res.exit_code == 0, res.error_tail
        assert counters.bisync_successful, (tmp_path / "dry.log").read_text()
        assert counters.planned == [], counters.planned
        assert (counters.deletions, counters.uploaded, counters.downloaded) == (0, 0, 0)
        assert int(counters.final_stats["deletes"]) == 0
        assert int(counters.final_stats["transfers"]) == 0

        # The real cycle through the engine: dry run, gate, real run.
        await engine.run_pair(pair_id, "APP")
        with sf() as db:
            jobs = db.scalars(select(SyncJob).order_by(SyncJob.id)).all()
            assert [(j.kind, j.state) for j in jobs] == [
                ("DRY_RUN", "SUCCESS"),
                ("BISYNC", "SUCCESS"),
            ], [j.error_message for j in jobs]
            assert jobs[0].planned_deletions == 0
            assert jobs[1].files_changed == 0 and jobs[1].conflicts == 0
            assert db.scalars(select(Conflict)).all() == []
            assert db.get(SyncPair, pair_id).status == "HEALTHY"
        for side in (local_dir, remote_dir):
            assert sorted(p.name for p in side.iterdir() if p.name != "RCLONE_TEST") == [
                "New",
                *[f"f{i}.txt" for i in range(others)],
            ]
    finally:
        await queue.stop()
        eng.dispose()


async def test_real_stat_of_a_missing_path_reaches_the_error_tail(tmp_path):
    conf = tmp_path / "rclone.conf"
    conf.write_text("[gd_t]\ntype = local\n")
    env = c.RcloneEnv(
        binary="rclone", config_file=conf, cache_dir=tmp_path / "cache", config_pass=""
    )
    res = await run_rclone(
        c.lsjson_args(env, f"gd_t:{tmp_path}/nope", stat=True), env.env(), log_path=None
    )
    assert res.exit_code == 3
    assert any("not found" in ln.lower() for ln in res.error_tail), res.error_tail


async def test_real_catalog_of_a_missing_drive_folder_is_an_empty_listing(tmp_path):
    from sqlalchemy.orm import sessionmaker

    from app.database.engine import make_engine
    from app.database.migrate import run_migrations
    from app.models import Account, SyncPair
    from app.paths import Layout
    from app.services.catalog.service import CatalogService
    from app.services.events.bus import EventBus

    class _Accounts:
        async def mark_needs_reauth(self, *a):
            raise AssertionError("no login problem expected")

    layout = Layout(tmp_path / "root")
    layout.ensure()
    run_migrations(layout.db_file)
    eng = make_engine(layout.db_file)
    sf = sessionmaker(eng, expire_on_commit=False)
    env = c.RcloneEnv(
        binary="rclone",
        config_file=layout.rclone_conf,
        cache_dir=layout.rclone_cache,
        config_pass="",
    )
    layout.rclone_conf.write_text("[gd_alice]\ntype = local\n")
    try:
        with sf() as db:
            acc = Account(email="alice@example.com", remote_name="gd_alice")
            db.add(acc)
            db.commit()
            pair = SyncPair(
                account_id=acc.id,
                name="Docs",
                local_subpath="",
                remote_path=str(tmp_path / "not-created-yet"),
            )
            db.add(pair)
            db.commit()
            pair_id = pair.id
        catalog = CatalogService(
            layout=layout, session_factory=sf, bus=EventBus(), rclone_env=env, accounts=_Accounts()
        )
        listing = await catalog.refresh(pair_id)
        assert listing is not None, catalog.last_error
        assert listing.files == {}
    finally:
        eng.dispose()


async def test_real_same_new_file_on_both_sides_with_different_times_is_no_conflict(tmp_path):
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from app.database.engine import make_engine
    from app.database.migrate import run_migrations
    from app.models import Account, Conflict, SyncJob, SyncPair
    from app.paths import Layout
    from app.services.events.bus import EventBus
    from app.services.sync.engine import SyncEngine
    from app.services.sync.filters import refresh_pair_filters

    class _Accounts:
        async def mark_needs_reauth(self, *a):
            raise AssertionError("no login problem expected")

    layout = Layout(tmp_path / "root")
    layout.ensure()
    run_migrations(layout.db_file)
    eng = make_engine(layout.db_file)
    sf = sessionmaker(eng, expire_on_commit=False)
    env = c.RcloneEnv(
        binary="rclone",
        config_file=layout.rclone_conf,
        cache_dir=layout.rclone_cache,
        config_pass="",
    )
    layout.rclone_conf.write_text("[gd_alice]\ntype = local\n")
    remote_dir = tmp_path / "drive"
    engine = SyncEngine(
        layout=layout, session_factory=sf, bus=EventBus(), rclone_env=env, accounts=_Accounts()
    )
    try:
        with sf() as db:
            acc = Account(email="alice@example.com", remote_name="gd_alice")
            db.add(acc)
            db.commit()
            pair = SyncPair(
                account_id=acc.id,
                name="Docs",
                local_subpath="",
                remote_path=str(remote_dir),
                initialized=True,
                status="HEALTHY",
            )
            db.add(pair)
            db.commit()
            refresh_pair_filters(db, layout, pair)
            local_dir = layout.account_dir(acc.email)
            for d in (local_dir, remote_dir):
                d.mkdir(parents=True, exist_ok=True)
                (d / "RCLONE_TEST").write_text("x")
                (d / "keep.txt").write_text("k")
            seed = await run_rclone(
                c.bisync_args(env, engine.bisync_target(acc, pair), dry_run=False, resync=True),
                env.env(),
                log_path=None,
            )
            assert seed.exit_code == 0, seed.error_tail
            pair_id = pair.id
        (local_dir / "s.txt").write_text("same")
        (remote_dir / "s.txt").write_text("same")
        os.utime(remote_dir / "s.txt", (1_600_000_000, 1_600_000_000))
        await engine.run_pair(pair_id, "MANUAL")
        with sf() as db:
            jobs = db.scalars(select(SyncJob).order_by(SyncJob.id)).all()
            assert [(j.kind, j.state) for j in jobs] == [
                ("DRY_RUN", "SUCCESS"),
                ("BISYNC", "SUCCESS"),
            ], [j.error_message for j in jobs]
            assert jobs[1].conflicts == 0
            assert db.scalars(select(Conflict)).all() == []
            assert db.get(SyncPair, pair_id).status == "HEALTHY"
        assert sorted(p.name for p in local_dir.iterdir()) == ["RCLONE_TEST", "keep.txt", "s.txt"]
    finally:
        eng.dispose()
