from pathlib import Path

import pytest

from app.services.rclone import commands as c
from app.services.rclone.commands import UnsafeArgument

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
    "--modify-window",
    "1s",
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
NO_PROMPT = "--ask-password=false"


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
    assert args[8 + len(STANDARD) : -1] == c.LOG_FLAGS
    assert args[-1] == NO_PROMPT
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


def test_bisync_max_delete_percent_custom_value_is_emitted():
    args = c.bisync_args(ENV, T, dry_run=False, max_delete_percent=10)
    i = args.index("--max-delete")
    assert args[i + 1] == "10"


@pytest.mark.parametrize("bad", [0, -5, 101, True, 2.5, "50"])
def test_bisync_max_delete_percent_out_of_range_is_rejected(bad):
    with pytest.raises(ValueError):
        c.bisync_args(ENV, T, dry_run=False, max_delete_percent=bad)


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
        "--filter-from",
        "--drive-skip-gdocs",
        "--dry-run",
        "--backup-dir",
        "--create-empty-src-dirs",
    ):
        assert flag in args


def test_one_way_sync_without_backup_dir_omits_flag():
    args = c.sync_args(ENV, "/d/l/Docs", "gd_1:Docs", T.filters_file, None, dry_run=False)
    assert "--backup-dir" not in args
    assert args[5:8] == ["sync", "/d/l/Docs", "gd_1:Docs"]


def test_one_way_sync_backup_dir_on_remote_side_is_rejected():
    with pytest.raises(UnsafeArgument, match="destination side"):
        c.sync_args(
            ENV,
            "/d/l/Docs",
            "gd_1:Docs",
            T.filters_file,
            Path("/d/app/data/backups/x"),
            dry_run=False,
        )


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
    assert args[-2] == "--non-interactive"
    assert args[-1] == NO_PROMPT


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


@pytest.mark.parametrize(
    "builder",
    [
        lambda: c.bisync_args(ENV, T, dry_run=True, resync=True),
        lambda: c.sync_args(ENV, "/l", "gd_1:Docs", T.filters_file, None, dry_run=True),
        lambda: c.check_args(ENV, Path("/l"), "gd_1:", Path("/f")),
        lambda: c.lsjson_args(ENV, "gd_1:"),
        lambda: c.about_args(ENV, "gd_1"),
        lambda: c.touch_args(ENV, "gd_1:Docs/a.txt"),
        lambda: c.version_args(ENV),
        lambda: c.config_create_args(ENV, "gd_1", "cid", "csec", "{}"),
        lambda: c.config_delete_args(ENV, "gd_1"),
        lambda: c.config_encrypt_args(
            c.RcloneEnv(
                binary="rclone",
                config_file=Path("/c"),
                cache_dir=Path("/k"),
                config_pass="",
                password_command="cat /run/pw",
            )
        ),
    ],
)
def test_every_builder_carries_no_prompt_flag(builder):
    args = builder()
    assert args[-1] == NO_PROMPT
    assert args.count(NO_PROMPT) == 1


def test_env_contains_config_pass_and_no_inherited_secrets(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "leak")
    env = ENV.env()
    assert env["RCLONE_CONFIG_PASS"] == "pw"
    assert "GOOGLE_CLIENT_SECRET" not in env


def test_env_home_is_the_rclone_dir_not_tmp():
    assert ENV.env()["HOME"] == "/d/app/data/rclone"


def test_env_omits_config_pass_when_empty():
    env = c.RcloneEnv(
        binary="rclone", config_file=Path("/c"), cache_dir=Path("/k"), config_pass=""
    ).env()
    assert "RCLONE_CONFIG_PASS" not in env


def test_sync_args_without_backup_dir_has_no_backup_flag_either_direction():
    up = c.sync_args(ENV, "/l/Docs", "gd_1:Docs", T.filters_file, None, dry_run=False)
    down = c.sync_args(ENV, "gd_1:Docs", "/l/Docs", T.filters_file, None, dry_run=False)
    assert "--backup-dir" not in up and "--backup-dir" not in down


def test_config_encrypt_requires_password_command():
    with pytest.raises(ValueError, match="password_command"):
        c.config_encrypt_args(ENV)


def test_config_encrypt_args_with_password_command():
    env = c.RcloneEnv(
        binary="rclone",
        config_file=Path("/c"),
        cache_dir=Path("/k"),
        config_pass="",
        password_command="cat /run/pw",
    )
    args = c.config_encrypt_args(env)
    assert args[5:8] == ["config", "encryption", "set"]
    i = args.index("--password-command")
    assert args[i + 1] == "cat /run/pw"


def test_touch_args():
    args = c.touch_args(ENV, "gd_1:Docs/a.txt")
    assert args[5:7] == ["touch", "gd_1:Docs/a.txt"]


def test_version_args():
    assert c.version_args(ENV) == ["rclone", "version", NO_PROMPT]


def test_lsjson_flags():
    args = c.lsjson_args(ENV, "gd_1:Docs", dirs_only=True, recursive=True, fast_list=True)
    assert args[5:7] == ["lsjson", "gd_1:Docs"]
    for flag in ("--dirs-only", "--recursive", "--fast-list"):
        assert flag in args
    plain = c.lsjson_args(ENV, "gd_1:")
    for flag in ("--dirs-only", "--recursive", "--fast-list"):
        assert flag not in plain


# --- guards: remote names, remote targets, local paths, secrets ----------------------


@pytest.mark.parametrize("name", ["a:b", "gd 1", "-rf", "", "gd-1", "gd\n"])
def test_remote_name_must_be_plain_word(name):
    with pytest.raises(UnsafeArgument):
        c.remote_target(name, "x")
    with pytest.raises(UnsafeArgument):
        c.about_args(ENV, name)
    with pytest.raises(UnsafeArgument):
        c.config_delete_args(ENV, name)
    with pytest.raises(UnsafeArgument):
        c.config_create_args(ENV, name, "cid", "csec", "{}")


@pytest.mark.parametrize("field", ["client_id", "client_secret", "token_json"])
@pytest.mark.parametrize("bad", ["--config=/x", "-rf", "a\nb", "a\x00b", "x\tb"])
def test_config_create_rejects_flag_like_or_control_secrets(field, bad):
    values = {"client_id": "cid", "client_secret": "csec", "token_json": "{}"}
    values[field] = bad
    with pytest.raises(UnsafeArgument):
        c.config_create_args(
            ENV, "gd_1", values["client_id"], values["client_secret"], values["token_json"]
        )


def test_client_secret_flag_injection_is_rejected_explicitly():
    with pytest.raises(UnsafeArgument, match="client_secret"):
        c.config_create_args(ENV, "gd_1", "cid", "--config=/x", "{}")


@pytest.mark.parametrize("target", ["-rf", "/abs/path", "Docs", "a b:c", ""])
def test_lsjson_target_must_be_remote(target):
    with pytest.raises(UnsafeArgument):
        c.lsjson_args(ENV, target)


def test_touch_target_must_be_remote():
    with pytest.raises(UnsafeArgument):
        c.touch_args(ENV, "-rf")


@pytest.mark.parametrize("local", ["relative/path", "-rf", "./x"])
def test_check_local_path_must_be_absolute(local):
    with pytest.raises(UnsafeArgument):
        c.check_args(ENV, Path(local), "gd_1:", Path("/f"))


def test_check_remote_must_look_like_remote():
    with pytest.raises(UnsafeArgument):
        c.check_args(ENV, Path("/l"), "-rf", Path("/f"))


def test_bisync_local_must_be_absolute():
    t = c.BisyncTarget(
        local=Path("relative/dir"),
        remote="gd_1:Docs",
        workdir=T.workdir,
        filters_file=T.filters_file,
        backup_dir=None,
    )
    with pytest.raises(UnsafeArgument):
        c.bisync_args(ENV, t, dry_run=False)


def test_bisync_remote_must_look_like_remote():
    t = c.BisyncTarget(
        local=T.local, remote="-rf", workdir=T.workdir, filters_file=T.filters_file, backup_dir=None
    )
    with pytest.raises(UnsafeArgument):
        c.bisync_args(ENV, t, dry_run=False)


@pytest.mark.parametrize(
    "src,dst", [("bogus", "gd_1:Docs"), ("gd_1:Docs", "bogus"), ("-rf", "/l"), ("/l", "-rf")]
)
def test_sync_endpoints_must_be_absolute_or_remote(src, dst):
    with pytest.raises(UnsafeArgument):
        c.sync_args(ENV, src, dst, T.filters_file, None, dry_run=True)


def test_bisync_and_check_accept_absolute_local_remote_side():
    t = c.BisyncTarget(
        local=Path("/d/a"),
        remote="/d/b",
        workdir=Path("/d/w"),
        filters_file=Path("/d/f"),
        backup_dir=None,
    )
    args = c.bisync_args(ENV, t, dry_run=True)
    assert args[args.index("bisync") + 2] == "/d/b"
    chk = c.check_args(ENV, Path("/d/a"), "/d/b", Path("/d/f"))
    assert chk[chk.index("check") + 2] == "/d/b"
    with pytest.raises(UnsafeArgument):
        c.check_args(ENV, Path("/d/a"), "relative/b", Path("/d/f"))


def test_sync_and_check_use_filter_from_not_filters_file():
    # --filters-file only exists on bisync; sync/check reject it as an unknown flag.
    sync = c.sync_args(ENV, "/l", "gd_1:Docs", Path("/f"), None, dry_run=True)
    chk = c.check_args(ENV, Path("/l"), "gd_1:", Path("/f"))
    for args in (sync, chk):
        assert "--filters-file" not in args
        assert args[args.index("--filter-from") + 1] == "/f"
    assert "--filters-file" in c.bisync_args(ENV, T, dry_run=True)


def test_one_way_sync_max_delete_is_emitted():
    args = c.sync_args(
        ENV, "/l/Docs", "gd_1:Docs", T.filters_file, None, dry_run=False, max_delete=4
    )
    assert args[args.index("--max-delete") + 1] == "4"
    assert "--max-delete" not in c.sync_args(
        ENV, "/l/Docs", "gd_1:Docs", T.filters_file, None, dry_run=False
    )
    zero = c.sync_args(
        ENV, "/l/Docs", "gd_1:Docs", T.filters_file, None, dry_run=False, max_delete=0
    )
    assert zero[zero.index("--max-delete") + 1] == "0"


@pytest.mark.parametrize("bad", [-1, True, 1.5, "3"])
def test_one_way_sync_max_delete_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        c.sync_args(ENV, "/l", "gd_1:Docs", T.filters_file, None, dry_run=False, max_delete=bad)


# --- Plan 2: catalog listing, saved-login dump, local pretend accounts ------------------


def test_lsjson_catalog_flags():
    args = c.lsjson_args(
        ENV, "gd_1:Docs", recursive=True, fast_list=True, metadata=True, show_all_gdocs=True
    )
    assert args[5:7] == ["lsjson", "gd_1:Docs"]
    for flag in ("--recursive", "--fast-list", "--metadata", "--drive-show-all-gdocs"):
        assert flag in args
    assert "--stat" not in args
    assert args[-1] == NO_PROMPT


def test_lsjson_stat_flag():
    args = c.lsjson_args(ENV, "gd_1:Docs", stat=True)
    assert "--stat" in args and "--metadata" not in args


def test_config_dump_args():
    assert c.config_dump_args(ENV)[5:] == ["config", "dump", NO_PROMPT]


def test_config_create_alias_args():
    args = c.config_create_alias_args(ENV, "local_1", Path("/srv/pretend drive"))
    assert args[5:] == [
        "config",
        "create",
        "local_1",
        "alias",
        "remote",
        "/srv/pretend drive",
        "--non-interactive",
        NO_PROMPT,
    ]


@pytest.mark.parametrize(
    "call",
    [
        lambda: c.config_create_alias_args(ENV, "bad name", Path("/x")),
        lambda: c.config_create_alias_args(ENV, "ok", Path("relative/dir")),
    ],
)
def test_config_create_alias_args_guards(call):
    with pytest.raises(UnsafeArgument):
        call()


def test_moveto_carries_trash_flag_and_needs_remote_targets():
    args = c.moveto_args(ENV, "gd_1:Docs/Old", "gd_1:Docs/New")
    assert args[5:8] == ["moveto", "gd_1:Docs/Old", "gd_1:Docs/New"]
    assert args[5] in c.DELETE_CAPABLE
    assert c.TRASH_FLAG in args
    assert args[-1] == NO_PROMPT and args.count(NO_PROMPT) == 1
    for bad in ("/local/path", "-rf", "Docs/Old"):
        with pytest.raises(c.UnsafeArgument):
            c.moveto_args(ENV, bad, "gd_1:x")
        with pytest.raises(c.UnsafeArgument):
            c.moveto_args(ENV, "gd_1:x", bad)


@pytest.mark.parametrize("bad", ["/srv/a\nroot = x", "/srv/a\rb", "/srv/a\x00b", "/srv/a\tb"])
def test_config_create_alias_rejects_control_characters_in_the_folder(bad):
    with pytest.raises(UnsafeArgument):
        c.config_create_alias_args(ENV, "local_1", Path(bad))


def test_time_comparing_commands_allow_one_second_of_modtime_difference():
    # Some disks keep only whole seconds of a file's modified time (Docker Desktop's
    # Mac file sharing truncates every time set on it; Drive keeps milliseconds). Without
    # a window, a resync fails its listing check and the next run sees every file as
    # changed on both sides.
    env = c.RcloneEnv("rclone", Path("/c/rclone.conf"), Path("/c/cache"), "pw")
    t = c.BisyncTarget(Path("/s/a"), "gd_1:Docs", Path("/w"), Path("/f.txt"), None)
    built = [
        c.bisync_args(env, t, dry_run=False),
        c.bisync_args(env, t, dry_run=True, resync=True),
        c.sync_args(env, "gd_1:Docs", "/s/a", Path("/f.txt"), None, dry_run=False),
        c.check_args(env, Path("/s/a"), "gd_1:Docs", Path("/f.txt")),
    ]
    for args in built:
        i = args.index("--modify-window")
        assert args[i + 1] == "1s"
