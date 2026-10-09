"""Every rclone command DriveSync runs is built here. Nothing else may build rclone args.

Backup note: the backup dir (--backup-dir1 / --backup-dir) only keeps local-side
overwrites and deletions. Drive-side changes are protected by Drive trash and
file revisions, not by this directory.
"""

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.services.files.scope import UPLOAD_TEMP_PREFIX
from app.services.sync.filters import escape_filter_path

TRASH_FLAG = "--drive-use-trash=true"
NO_PROMPT_FLAG = "--ask-password=false"
# Some disks keep only whole seconds of a modified time (Docker Desktop's Mac file sharing
# truncates every time set on it); Drive keeps milliseconds. Without this, a resync fails its
# listing check and the next run treats every file as changed on both sides.
MODIFY_WINDOW = ["--modify-window", "1s"]
LOG_FLAGS = [
    "--use-json-log",
    "--log-level",
    "INFO",
    "--stats",
    "1s",
    "--stats-log-level",
    "NOTICE",
]
DELETE_CAPABLE = frozenset(
    {"bisync", "sync", "delete", "purge", "deletefile", "move", "moveto", "rmdirs"}
)

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
        *MODIFY_WINDOW,
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
    max_delete: int | None = None,
) -> list[str]:
    if max_delete is not None and (
        isinstance(max_delete, bool) or not isinstance(max_delete, int) or max_delete < 0
    ):
        raise ValueError(f"max_delete must be an int >= 0: {max_delete!r}")
    src_ep = _endpoint(src)
    dst_ep = _endpoint(dst)
    if backup_dir is not None and not dst_ep.startswith("/"):
        raise UnsafeArgument("backup-dir must be on the destination side")
    args = env.base() + [
        "sync",
        src_ep,
        dst_ep,
        "--filter-from",
        str(filters_file),
        *MODIFY_WINDOW,
        TRASH_FLAG,
        "--drive-skip-gdocs",
        "--create-empty-src-dirs",
    ]
    if backup_dir is not None:
        args += ["--backup-dir", str(backup_dir)]
    if max_delete is not None:
        args += ["--max-delete", str(max_delete)]
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
        "--filter-from",
        str(filters_file),
        *MODIFY_WINDOW,
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
    metadata: bool = False,
    stat: bool = False,
    show_all_gdocs: bool = False,
) -> list[str]:
    args = env.base() + ["lsjson", _remote(target)]
    if dirs_only:
        args.append("--dirs-only")
    if recursive:
        args.append("--recursive")
    if fast_list:
        args.append("--fast-list")
    if metadata:
        args.append("--metadata")  # Drive adds e.g. "starred" here
    if stat:
        args.append("--stat")  # describe the target itself, not its contents
    if show_all_gdocs:
        args.append("--drive-show-all-gdocs")  # also list Forms, Sites, ... (no export format)
    return args + [NO_PROMPT_FLAG]


def moveto_args(env: RcloneEnv, src: str, dst: str) -> list[str]:
    """Move one file or folder within Drive. A server-side move keeps file IDs and sharing."""
    return env.base() + [
        "moveto",
        _remote(src),
        _remote(dst),
        TRASH_FLAG,
        *LOG_FLAGS,
        NO_PROMPT_FLAG,
    ]


def _remote_item(target: str) -> str:
    """A remote target naming one item below the remote's top: never the whole Drive."""
    _remote(target)
    path = target.split(":", 1)[1]
    if not path.strip("/") or _CONTROL_CHARS.search(path):
        raise UnsafeArgument(f"target must name one item, not the whole remote: {target!r}")
    return target


def item_filters(name: str, is_dir: bool) -> list[str]:
    """Limit a command run on a parent folder to one item (and, for a folder, what is in it).

    Upload copies being written are left out first; then the item; then nothing else.
    """
    if not name or "/" in name or name in (".", ".."):
        raise UnsafeArgument(f"not a single item name: {name!r}")
    try:
        esc = escape_filter_path(name)
    except ValueError as e:
        raise UnsafeArgument(str(e)) from e
    include = f"+ /{esc}/**" if is_dir else f"+ /{esc}"
    return [f"--filter=- {UPLOAD_TEMP_PREFIX}*", f"--filter={include}", "--filter=- **"]


def offload_list_args(env: RcloneEnv, remote_parent: str, name: str, is_dir: bool) -> list[str]:
    """Every file of one item on Drive, with Drive IDs and MD5s. Native docs are left out."""
    return env.base() + [
        "lsjson",
        _remote(remote_parent),
        "--recursive",
        "--files-only",
        "--hash",
        "--hash-type",
        "md5",
        "--drive-skip-gdocs",
        *item_filters(name, is_dir),
        NO_PROMPT_FLAG,
    ]


def offload_check_args(
    env: RcloneEnv, local_parent: Path, remote_parent: str, name: str, is_dir: bool
) -> list[str]:
    """Compare one item here with Drive (size and checksum), one line per file."""
    return env.base() + [
        "check",
        _local(local_parent),
        _endpoint(remote_parent),
        "--combined",
        "-",
        *item_filters(name, is_dir),
        *MODIFY_WINDOW,
        "--drive-skip-gdocs",
        "--use-json-log",
        "--log-level",
        "NOTICE",
        NO_PROMPT_FLAG,
    ]


def files_from_raw_text(rel_paths: Sequence[str]) -> str:
    """The checked list for delete_args: one path per line, relative to the deleted folder.

    A name holding a line break would read as two paths, and an absolute path or a `..` part
    could point outside the folder, so such lists are refused. An empty list is refused too:
    there is nothing to delete, and the caller should not run a delete at all.
    """
    if not rel_paths:
        raise UnsafeArgument("the list of files to delete is empty")
    for rel in rel_paths:
        parts = rel.split("/")
        if (
            not rel
            or _CONTROL_CHARS.search(rel)
            or rel.startswith("/")
            or any(part in ("", ".", "..") for part in parts)
        ):
            raise UnsafeArgument(f"not a usable path for the delete list: {rel!r}")
    return "".join(f"{rel}\n" for rel in rel_paths)


def deletefile_args(env: RcloneEnv, target: str) -> list[str]:
    """One file, into Drive's trash."""
    return env.base() + ["deletefile", _remote_item(target), TRASH_FLAG, *LOG_FLAGS, NO_PROMPT_FLAG]


def delete_args(env: RcloneEnv, target: str, files_from: Path) -> list[str]:
    """Only the listed files of a folder (see files_from_raw_text), into Drive's trash.

    Never "whatever is in the folder now": a file added on another device after the check is
    not in the list and stays. Native docs are skipped and stay.
    """
    return env.base() + [
        "delete",
        _remote_item(target),
        "--files-from-raw",
        _local(files_from),
        TRASH_FLAG,
        "--drive-skip-gdocs",
        *LOG_FLAGS,
        NO_PROMPT_FLAG,
    ]


def rmdirs_args(env: RcloneEnv, target: str) -> list[str]:
    """Remove the empty folders left by delete_args (a folder still holding anything stays).

    No --drive-skip-gdocs here, so a folder that still holds a native doc is not seen as empty.
    """
    return env.base() + ["rmdirs", _remote_item(target), TRASH_FLAG, *LOG_FLAGS, NO_PROMPT_FLAG]


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


def config_create_alias_args(env: RcloneEnv, name: str, folder: Path) -> list[str]:
    """A remote that is just a local folder: used by the dev/test 'pretend Google account'."""
    # A newline in a config value could add keys to the remote's section.
    folder_text = _secret("folder", _local(folder))
    return env.base() + [
        "config",
        "create",
        _remote_name(name),
        "alias",
        "remote",
        folder_text,
        "--non-interactive",
        NO_PROMPT_FLAG,
    ]


def config_dump_args(env: RcloneEnv) -> list[str]:
    # Prints every saved login, decrypted. Only drive_api reads it, never logs it.
    return env.base() + ["config", "dump", NO_PROMPT_FLAG]


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
