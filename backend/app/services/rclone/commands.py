"""Every rclone command DriveSync runs is built here. Nothing else may build rclone args.

Backup note: the backup dir (--backup-dir1 / --backup-dir) only keeps local-side
overwrites and deletions. Drive-side changes are protected by Drive trash and
file revisions, not by this directory.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

TRASH_FLAG = "--drive-use-trash=true"
NO_PROMPT_FLAG = "--ask-password=false"
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

_REMOTE_NAME = re.compile(r"[A-Za-z0-9_]+")
_REMOTE_TARGET = re.compile(r"[A-Za-z0-9_]+:")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f]")


class UnsafeArgument(ValueError):
    """A value would let rclone read a flag or a path it should not."""


def _remote_name(name: str) -> str:
    if not _REMOTE_NAME.fullmatch(name):
        raise UnsafeArgument(f"invalid remote name: {name!r}")
    return name


def _local(path: Path) -> str:
    text = str(path)
    if not text.startswith("/"):
        raise UnsafeArgument(f"local path must be absolute: {text!r}")
    return text


def _remote(target: str) -> str:
    if not _REMOTE_TARGET.match(target):
        raise UnsafeArgument(f"remote target must look like name:path: {target!r}")
    return target


def _endpoint(value: str) -> str:
    if value.startswith("/"):
        return value
    return _remote(value)


def _secret(label: str, value: str) -> str:
    if value.startswith("-") or _CONTROL_CHARS.search(value):
        raise UnsafeArgument(f"{label} must not start with '-' or contain control characters")
    return value


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
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(self.cache_dir.parent),
        }
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
    return f"{_remote_name(remote_name)}:{path}"


def bisync_args(
    env: RcloneEnv,
    t: BisyncTarget,
    *,
    dry_run: bool,
    resync: bool = False,
    max_delete_percent: int = 50,
) -> list[str]:
    if (
        isinstance(max_delete_percent, bool)
        or not isinstance(max_delete_percent, int)
        or not 1 <= max_delete_percent <= 100
    ):
        raise ValueError(f"max_delete_percent must be an int 1..100: {max_delete_percent!r}")
    args = env.base() + [
        "bisync",
        _local(t.local),
        _endpoint(t.remote),
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
    return args + [NO_PROMPT_FLAG]


def sync_args(
    env: RcloneEnv,
    src: str,
    dst: str,
    filters_file: Path,
    backup_dir: Path | None,
    *,
    dry_run: bool,
) -> list[str]:
    src_ep = _endpoint(src)
    dst_ep = _endpoint(dst)
    if backup_dir is not None and not dst_ep.startswith("/"):
        raise UnsafeArgument("backup-dir must be on the destination side")
    args = env.base() + [
        "sync",
        src_ep,
        dst_ep,
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
    return args + [NO_PROMPT_FLAG]


def check_args(env: RcloneEnv, local: Path, remote: str, filters_file: Path) -> list[str]:
    return env.base() + [
        "check",
        _local(local),
        _endpoint(remote),
        "--combined",
        "-",
        "--filters-file",
        str(filters_file),
        "--drive-skip-gdocs",
        "--use-json-log",
        "--log-level",
        "NOTICE",
        NO_PROMPT_FLAG,
    ]


def lsjson_args(
    env: RcloneEnv,
    target: str,
    *,
    dirs_only: bool = False,
    recursive: bool = False,
    fast_list: bool = False,
) -> list[str]:
    args = env.base() + ["lsjson", _remote(target)]
    if dirs_only:
        args.append("--dirs-only")
    if recursive:
        args.append("--recursive")
    if fast_list:
        args.append("--fast-list")
    return args + [NO_PROMPT_FLAG]


def about_args(env: RcloneEnv, remote_name: str) -> list[str]:
    return env.base() + ["about", f"{_remote_name(remote_name)}:", "--json", NO_PROMPT_FLAG]


def touch_args(env: RcloneEnv, target: str) -> list[str]:
    return env.base() + ["touch", _remote(target), NO_PROMPT_FLAG]


def version_args(env: RcloneEnv) -> list[str]:
    return [env.binary, "version", NO_PROMPT_FLAG]


def config_create_args(
    env: RcloneEnv, name: str, client_id: str, client_secret: str, token_json: str
) -> list[str]:
    # rclone offers no non-interactive way to pass these other than argv; only processes
    # inside this container can see them, and the runner never logs argv.
    _secret("client_id", client_id)
    _secret("client_secret", client_secret)
    _secret("token_json", token_json)
    return env.base() + [
        "config",
        "create",
        _remote_name(name),
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
        NO_PROMPT_FLAG,
    ]


def config_delete_args(env: RcloneEnv, name: str) -> list[str]:
    return env.base() + ["config", "delete", _remote_name(name), NO_PROMPT_FLAG]


def config_encrypt_args(env: RcloneEnv) -> list[str]:
    if not env.password_command:
        raise ValueError("password_command is required to encrypt the config")
    return env.base() + [
        "config",
        "encryption",
        "set",
        "--password-command",
        env.password_command,
        NO_PROMPT_FLAG,
    ]
