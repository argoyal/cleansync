"""Every rclone command DriveSync runs is built here. Nothing else may build rclone args."""

import os
from dataclasses import dataclass
from pathlib import Path

TRASH_FLAG = "--drive-use-trash=true"
LOG_FLAGS = [
    "--use-json-log",
    "--log-level",
    "INFO",
    "--stats",
    "1s",
    "--stats-log-level",
    "NOTICE",
]
DELETE_CAPABLE = frozenset({"bisync", "sync", "delete", "purge", "deletefile", "move", "moveto"})


@dataclass(frozen=True)
class RcloneEnv:
    binary: str
    config_file: Path
    cache_dir: Path
    config_pass: str
    password_command: str | None = None

    def base(self) -> list[str]:
        return [self.binary, "--config", str(self.config_file), "--cache-dir", str(self.cache_dir)]

    def env(self) -> dict[str, str]:
        env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": "/tmp"}  # noqa: S108 - rclone only needs a writable HOME
        if self.config_pass:
            env["RCLONE_CONFIG_PASS"] = self.config_pass
        return env


@dataclass(frozen=True)
class BisyncTarget:
    local: Path
    remote: str
    workdir: Path
    filters_file: Path
    backup_dir: Path | None


def remote_target(remote_name: str, path: str) -> str:
    return f"{remote_name}:{path}"


def bisync_args(
    env: RcloneEnv,
    t: BisyncTarget,
    *,
    dry_run: bool,
    resync: bool = False,
    max_delete_percent: int = 50,
) -> list[str]:
    args = env.base() + [
        "bisync",
        str(t.local),
        t.remote,
        "--check-access",
        "--check-filename",
        "RCLONE_TEST",
        "--filters-file",
        str(t.filters_file),
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
        str(max_delete_percent),
        "--create-empty-src-dirs",
        TRASH_FLAG,
        "--drive-skip-gdocs",
        "--workdir",
        str(t.workdir),
    ]
    if t.backup_dir is not None:
        args += ["--backup-dir1", str(t.backup_dir)]
    args += LOG_FLAGS
    if resync:
        args += ["--resync", "--resync-mode", "newer"]
    if dry_run:
        args.append("--dry-run")
    return args


def sync_args(
    env: RcloneEnv,
    src: str,
    dst: str,
    filters_file: Path,
    backup_dir: Path | None,
    *,
    dry_run: bool,
) -> list[str]:
    args = env.base() + [
        "sync",
        src,
        dst,
        "--filters-file",
        str(filters_file),
        TRASH_FLAG,
        "--drive-skip-gdocs",
        "--create-empty-src-dirs",
    ]
    if backup_dir is not None:
        args += ["--backup-dir", str(backup_dir)]
    args += LOG_FLAGS
    if dry_run:
        args.append("--dry-run")
    return args


def check_args(env: RcloneEnv, local: Path, remote: str, filters_file: Path) -> list[str]:
    return env.base() + [
        "check",
        str(local),
        remote,
        "--combined",
        "-",
        "--filters-file",
        str(filters_file),
        "--drive-skip-gdocs",
        "--use-json-log",
        "--log-level",
        "NOTICE",
    ]


def lsjson_args(
    env: RcloneEnv,
    target: str,
    *,
    dirs_only: bool = False,
    recursive: bool = False,
    fast_list: bool = False,
) -> list[str]:
    args = env.base() + ["lsjson", target]
    if dirs_only:
        args.append("--dirs-only")
    if recursive:
        args.append("--recursive")
    if fast_list:
        args.append("--fast-list")
    return args


def about_args(env: RcloneEnv, remote_name: str) -> list[str]:
    return env.base() + ["about", f"{remote_name}:", "--json"]


def touch_args(env: RcloneEnv, target: str) -> list[str]:
    return env.base() + ["touch", target]


def version_args(env: RcloneEnv) -> list[str]:
    return [env.binary, "version"]


def config_create_args(
    env: RcloneEnv, name: str, client_id: str, client_secret: str, token_json: str
) -> list[str]:
    return env.base() + [
        "config",
        "create",
        name,
        "drive",
        "client_id",
        client_id,
        "client_secret",
        client_secret,
        "scope",
        "drive",
        "use_trash",
        "true",
        "token",
        token_json,
        "--non-interactive",
    ]


def config_delete_args(env: RcloneEnv, name: str) -> list[str]:
    return env.base() + ["config", "delete", name]


def config_encrypt_args(env: RcloneEnv) -> list[str]:
    if not env.password_command:
        raise ValueError("password_command is required to encrypt the config")
    return env.base() + ["config", "encryption", "set", "--password-command", env.password_command]
