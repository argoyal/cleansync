from pathlib import Path

import pytest

from app.services.rclone import commands as c

ENV = c.RcloneEnv(
    binary="rclone",
    config_file=Path("/d/app/data/rclone/rclone.conf"),
    cache_dir=Path("/d/app/data/rclone/cache"),
    config_pass="pw",
)
T = c.BisyncTarget(
    local=Path("/d/storage/alice@example.com/Work Stuff"),
    remote="gd_1:Work Stuff",
    workdir=Path("/d/app/data/rclone/bisync/1"),
    filters_file=Path("/d/app/data/filters/1.txt"),
    backup_dir=Path("/d/app/data/backups/alice@example.com/1/2026-10-08"),
)

STANDARD = [
    "--check-access",
    "--check-filename",
    "RCLONE_TEST",
    "--filters-file",
    "/d/app/data/filters/1.txt",
    "--compare",
    "size,modtime,checksum",
    "--slow-hash-sync-only",
    "--conflict-resolve",
    "none",
    "--conflict-loser",
    "num",
    "--conflict-suffix",
    "conflict",
    "--suffix-keep-extension",
    "--resilient",
    "--recover",
    "--max-lock",
    "2m",
    "--max-delete",
    "50",
    "--create-empty-src-dirs",
    "--drive-use-trash=true",
    "--drive-skip-gdocs",
    "--workdir",
    "/d/app/data/rclone/bisync/1",
    "--backup-dir1",
    "/d/app/data/backups/alice@example.com/1/2026-10-08",
]


def test_bisync_has_exactly_the_standard_flags():
    args = c.bisync_args(ENV, T, dry_run=False)
    assert args[:6] == [
        "rclone",
        "--config",
        "/d/app/data/rclone/rclone.conf",
        "--cache-dir",
        "/d/app/data/rclone/cache",
        "bisync",
    ]
    assert args[6:8] == ["/d/storage/alice@example.com/Work Stuff", "gd_1:Work Stuff"]
    assert args[8 : 8 + len(STANDARD)] == STANDARD
    assert args[8 + len(STANDARD) :] == c.LOG_FLAGS
    assert "--dry-run" not in args and "--resync" not in args


def test_bisync_dry_run_and_resync_flags():
    assert "--dry-run" in c.bisync_args(ENV, T, dry_run=True)
    args = c.bisync_args(ENV, T, dry_run=False, resync=True)
    i = args.index("--resync")
    assert args[i + 1 : i + 3] == ["--resync-mode", "newer"]


def test_bisync_without_backups_has_no_backup_dir():
    t = c.BisyncTarget(
        local=T.local,
        remote=T.remote,
        workdir=T.workdir,
        filters_file=T.filters_file,
        backup_dir=None,
    )
    assert "--backup-dir1" not in c.bisync_args(ENV, t, dry_run=False)


def test_paths_with_spaces_and_dashes_are_single_args():
    t = c.BisyncTarget(
        local=Path("/d/storage/a@b.co/-rf [x] *"),
        remote="gd_1:-rf [x] *",
        workdir=T.workdir,
        filters_file=T.filters_file,
        backup_dir=None,
    )
    args = c.bisync_args(ENV, t, dry_run=False)
    assert "/d/storage/a@b.co/-rf [x] *" in args
    assert "gd_1:-rf [x] *" in args
    assert not any(a.startswith("-rf") for a in args)


def test_every_delete_capable_command_carries_trash_flag():
    built = [
        c.bisync_args(ENV, T, dry_run=False),
        c.bisync_args(ENV, T, dry_run=True, resync=True),
        c.sync_args(
            ENV, "gd_1:Docs", "/d/storage/a@b.co/Docs", T.filters_file, None, dry_run=False
        ),
        c.sync_args(ENV, "/d/storage/a@b.co/Docs", "gd_1:Docs", T.filters_file, None, dry_run=True),
    ]
    for args in built:
        sub = args[5]
        assert sub in c.DELETE_CAPABLE
        assert c.TRASH_FLAG in args, sub


def test_one_way_sync_flags():
    args = c.sync_args(
        ENV, "gd_1:Docs", "/d/l/Docs", T.filters_file, Path("/d/app/data/backups/x"), dry_run=True
    )
    assert args[5:8] == ["sync", "gd_1:Docs", "/d/l/Docs"]
    for flag in (
        "--filters-file",
        "--drive-skip-gdocs",
        "--dry-run",
        "--backup-dir",
        "--create-empty-src-dirs",
    ):
        assert flag in args


def test_remote_target_joins_cleanly():
    assert c.remote_target("gd_1", "") == "gd_1:"
    assert c.remote_target("gd_1", "Work/Projects") == "gd_1:Work/Projects"


def test_config_create_sets_trash_and_scope():
    args = c.config_create_args(ENV, "gd_1", "cid", "csecret", '{"access_token":"x"}')
    assert args[5:9] == ["config", "create", "gd_1", "drive"]
    tail = args[9:]
    pairs = dict(zip(tail[0:-1:2], tail[1::2], strict=False))
    assert pairs["scope"] == "drive"
    assert pairs["use_trash"] == "true"
    assert pairs["client_id"] == "cid"
    assert args[-1] == "--non-interactive"


@pytest.mark.parametrize(
    "builder",
    [
        lambda: c.check_args(ENV, Path("/l"), "gd_1:", Path("/f")),
        lambda: c.lsjson_args(ENV, "gd_1:", dirs_only=True),
        lambda: c.about_args(ENV, "gd_1"),
    ],
)
def test_read_only_commands_never_include_dry_run_or_delete(builder):
    args = builder()
    assert args[5] not in c.DELETE_CAPABLE


def test_env_contains_config_pass_and_no_inherited_secrets(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "leak")
    env = ENV.env()
    assert env["RCLONE_CONFIG_PASS"] == "pw"
    assert "GOOGLE_CLIENT_SECRET" not in env
