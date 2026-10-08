import shutil
import tempfile
from pathlib import Path

import pytest

from app.services.rclone import commands as c
from app.services.rclone.parse import Counters, count_dry_run_deletes, parse_check_combined
from app.services.rclone.runner import run_rclone

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
    skipped = [ln for ln in lines if ln.msg.startswith("Skipped ")]
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
