"""The Recovery view (spec §8): everything CleanSync kept that can be put back.

Entries are files under app/data/backups/<email>/<pair id or "unsynced">/<YYYY-MM-DD>/, plus
offloads whose files CleanSync sent to Google Drive's trash. An entry's id is its path under
backups/<email>/ (or "offload:<id>"). Restoring copies a file back; it never overwrites.

Offload lists (delete-<job>.txt) and offload manifests live in app/data/offloads/, not under
backups/, so they are never entries.
"""

import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path, PurePosixPath

from app.core.clock import utcnow
from app.core.pathsafe import UnsafePath, normalize_rel, safe_join
from app.models import Offload, SyncPair
from app.paths import Layout
from app.services.files.names import split_name
from app.services.files.ops import FileOpError, refuse_one_way
from app.services.files.scope import (
    Scope,
    is_hidden,
    is_hidden_name,
    join_rel,
    pair_for,
    parent_rel,
    resolves_to_hidden,
    to_account_rel,
    to_pair_rel,
)
from app.services.files.upload import _copy_beside, _place
from app.services.offload.store import Paths, covers
from app.services.sync.backups import UNSYNCED

KINDS = ("sync_backup", "deleted", "first_sync", "resync", "conflict", "offload")
OFFLOAD_PREFIX = "offload:"
GOOGLE_TRASH_DAYS = 30  # Google empties its trash after 30 days
PAIR_GONE_MSG = (
    "The sync folder this came from was removed, so CleanSync doesn't know where it goes. "
    "Download it instead."
)
OFFLOAD_MSG = (
    "This is in Google Drive's trash after an offload; the copy on this server is still in "
    "place. Use Restore to Drive on the Offloaded page."
)
FOLDER_GONE_MSG = "The folder it came from can't be used any more. Download it instead."
_DELETED = re.compile(r"\.drivesync-deleted-\d{6}-[0-9a-f]+")
_INITIAL = re.compile(r"\.drivesync-initial-\d+")
_RESYNC = re.compile(r"\.drivesync-resync-\d+")
_CONFLICT = (
    re.compile(r"^(?P<stem>.+)\.conflict\d+(?P<ext>\.[^.]+)$"),
    re.compile(r"^(?P<stem>.+)\.conflict\d+$"),
)


class RecoveryError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RecoveryEntry:
    id: str
    kind: str
    pair_id: int | None
    pair_name: str | None
    name: str
    original_path: str | None  # account path it came from; None when its pair was removed
    size: int | None
    day: date
    expires_on: date | None  # first day it may be cleaned up; None = kept until deleted
    offload_id: int | None = None
    # An offload past Google's 30 days: Google has emptied it from its trash by now.
    google_emptied: bool = False

    @property
    def restorable(self) -> bool:
        return self.kind != "offload" and self.original_path is not None


@dataclass
class RestoreResult:
    id: str
    restored_to: str | None = None
    error: str | None = None
    # Put back inside an offloaded folder: it stays out of sync with it, so no sync is queued.
    local_only: bool = False


def unconflict(name: str) -> str:
    """The name a conflict copy had before rclone renamed it ("a.conflict2.txt" -> "a.txt")."""
    for pattern in _CONFLICT:
        m = pattern.match(name)
        if m:
            return m["stem"] + (m.groupdict().get("ext") or "")
    return name


def restored_names(name: str) -> Iterator[str]:
    yield name
    stem, ext = split_name(name)
    yield f"{stem} (restored){ext}"
    for n in range(2, 1000):
        yield f"{stem} (restored {n}){ext}"


def _classify(unsynced: bool, parts: list[str]) -> tuple[str, list[str]] | None:
    first = parts[0]
    if _DELETED.fullmatch(first):
        kind, inner = "deleted", parts[1:]
    elif unsynced:
        return None  # nothing else in "unsynced" is CleanSync's
    elif _INITIAL.fullmatch(first):
        kind, inner = "first_sync", parts[1:]
    elif _RESYNC.fullmatch(first):
        # Saved before a safe resync (also when a Restore to Drive met a newer Drive file).
        kind, inner = "resync", parts[1:]
    elif first == "conflicts":
        inner = parts[1:]
        kind = "conflict"
        if inner:
            inner = [*inner[:-1], unconflict(inner[-1])]
    else:
        kind, inner = "sync_backup", parts
    return (kind, inner) if inner else None


def _valid_bucket(bucket: str) -> bool:
    return bucket == UNSYNCED or bucket.isdigit()


def _entry(
    pairs: dict[str, SyncPair],
    default_days: int | None,
    bucket: str,
    day: date,
    parts: list[str],
    size: int | None,
) -> RecoveryEntry | None:
    unsynced = bucket == UNSYNCED
    found = _classify(unsynced, parts)
    if found is None:
        return None
    kind, inner = found
    inner_rel = "/".join(inner)
    pair = None if unsynced else pairs.get(bucket)
    if unsynced:
        original: str | None = inner_rel
    elif pair is not None:
        original = to_account_rel(pair, inner_rel)
    else:
        original = None
    # Same rule as prune_backups: a pair's own retention, the default for "unsynced", and
    # folders of removed pairs are never pruned.
    days = default_days if unsynced else (pair.backup_retention_days if pair else None)
    return RecoveryEntry(
        id="/".join([bucket, day.isoformat(), *parts]),
        kind=kind,
        pair_id=pair.id if pair else None,
        pair_name=pair.name if pair else None,
        name=inner[-1],
        original_path=original,
        size=size,
        day=day,
        expires_on=day + timedelta(days=int(days) + 1) if days is not None else None,
    )


def _dirs(folder: Path) -> list[Path]:
    try:
        return sorted(p for p in folder.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return []


def _files(day_dir: Path) -> Iterator[tuple[Path, list[str]]]:
    # os.walk does not follow links into folders; linked files are skipped below.
    for dirpath, dirnames, filenames in os.walk(day_dir):
        dirnames[:] = sorted(d for d in dirnames if not is_hidden_name(d))
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if is_hidden_name(name) or full.is_symlink():
                continue
            yield full, list(full.relative_to(day_dir).parts)


def list_entries(
    layout: Layout,
    scope: Scope,
    *,
    default_days: int | None,
    offloads: list[tuple[Offload, SyncPair]],
    today: date | None = None,
) -> list[RecoveryEntry]:
    today = today or utcnow().date()
    root = layout.backups_dir / scope.account.email
    pairs = {str(p.id): p for p in scope.pairs}
    out: list[RecoveryEntry] = []
    for bucket_dir in _dirs(root) if root.is_dir() and not root.is_symlink() else []:
        if not _valid_bucket(bucket_dir.name):
            continue
        for day_dir in _dirs(bucket_dir):
            try:
                day = date.fromisoformat(day_dir.name)
            except ValueError:
                continue
            for path, parts in _files(day_dir):
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                e = _entry(pairs, default_days, bucket_dir.name, day, parts, size)
                if e is not None:
                    out.append(e)
    for off, pair in offloads:
        made = off.created_at.date()
        trash_ends = made + timedelta(days=GOOGLE_TRASH_DAYS)
        out.append(
            RecoveryEntry(
                id=f"{OFFLOAD_PREFIX}{off.id}",
                kind="offload",
                pair_id=pair.id,
                pair_name=pair.name,
                name=PurePosixPath(off.path).name,
                original_path=to_account_rel(pair, off.path),
                size=off.total_bytes,
                day=made,
                expires_on=trash_ends,
                offload_id=off.id,
                google_emptied=today >= trash_ends,
            )
        )
    out.sort(key=lambda e: (-e.day.toordinal(), e.original_path or e.id, e.id))
    return out


def check_filters(kind: str, path: str) -> str:
    """Raises RecoveryError(422) for an unknown kind or an unsafe path; returns the clean path."""
    if kind and kind not in KINDS:
        raise RecoveryError(f"unknown kind {kind!r}", 422)
    try:
        return normalize_rel(path)
    except UnsafePath as e:
        raise RecoveryError(str(e), 422) from e


def filter_entries(
    entries: list[RecoveryEntry], *, pair: str = "", kind: str = "", path: str = ""
) -> list[RecoveryEntry]:
    out = entries
    if pair == UNSYNCED:
        out = [e for e in out if e.pair_id is None]
    elif pair:
        out = [e for e in out if str(e.pair_id) == pair]
    if kind:
        out = [e for e in out if e.kind == kind]
    if path:
        out = [
            e
            for e in out
            if e.original_path
            and (e.original_path == path or e.original_path.startswith(path + "/"))
        ]
    return out


def _no_links(base: Path, rel: str) -> bool:
    """True when no part of base/rel is a symlink (the listing never goes through links)."""
    current = base
    for part in rel.split("/"):
        current = current / part
        if current.is_symlink():
            return False
    return True


def resolve(
    layout: Layout, scope: Scope, entry_id: str, *, default_days: int | None = None
) -> tuple[RecoveryEntry, Path]:
    """The entry and its file. Ids are checked: they can't reach outside backups/<email>/.
    default_days is the default retention, for the entry's expires_on."""
    if entry_id.startswith(OFFLOAD_PREFIX):
        raise RecoveryError(OFFLOAD_MSG)
    try:
        rel = normalize_rel(entry_id)
    except UnsafePath as e:
        raise RecoveryError(str(e), 422) from e
    parts = rel.split("/")
    if len(parts) < 3 or not _valid_bucket(parts[0]) or any(is_hidden_name(p) for p in parts):
        raise RecoveryError("not found", 404)
    try:
        day = date.fromisoformat(parts[1])
    except ValueError:
        raise RecoveryError("not found", 404) from None
    base = layout.backups_dir / scope.account.email
    try:
        src = safe_join(base, rel)
    except UnsafePath as e:
        raise RecoveryError(str(e), 422) from e
    if base.is_symlink() or not _no_links(base, rel) or not src.is_file():
        raise RecoveryError("not found", 404)
    pairs = {str(p.id): p for p in scope.pairs}
    entry = _entry(pairs, default_days, parts[0], day, parts[2:], src.stat().st_size)
    if entry is None:
        raise RecoveryError("not found", 404)
    return entry, src


def _usable_folder(scope: Scope, pair: SyncPair | None, folder_rel: str) -> Path:
    """The folder to restore into. Missing folders are made again, but only below the pair's
    own folder (or the account folder), which must still be there: if it is gone the disk is
    probably not mounted, and nothing should be written in its place."""
    base_rel = pair.local_subpath if pair is not None else ""
    try:
        base = safe_join(scope.root, base_rel)
        folder = safe_join(scope.root, folder_rel)
    except UnsafePath as e:
        raise RecoveryError(str(e), 422) from e
    if base.is_symlink() or not base.is_dir():
        raise RecoveryError(FOLDER_GONE_MSG)
    # Checked before making anything: a link on the way must not lead into a hidden folder.
    if resolves_to_hidden(scope.root, folder):
        raise RecoveryError(FOLDER_GONE_MSG)
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink() or not folder.is_dir() or resolves_to_hidden(scope.root, folder):
        raise RecoveryError(FOLDER_GONE_MSG)
    return folder


def _restore_one(
    layout: Layout, scope: Scope, entry_id: str, offloaded: Mapping[int, Paths]
) -> tuple[str, bool]:
    """Where it was put back, and whether that is inside an offloaded folder."""
    entry, src = resolve(layout, scope, entry_id)
    if entry.original_path is None:
        raise RecoveryError(PAIR_GONE_MSG)
    original = entry.original_path
    if is_hidden(original):
        raise RecoveryError("not found", 404)
    refuse_one_way(scope, [original])
    folder_rel = parent_rel(original)
    # Where it goes now decides (an "unsynced" item may since be inside a sync pair).
    pair = pair_for(scope.pairs, original)
    # Back into the offloaded folder it was deleted from: a local put-back only. The folder
    # stays out of sync (its filter covers the new file too) until Restore to Drive.
    in_offloaded = pair is not None and covers(
        offloaded.get(pair.id, ()), to_pair_rel(pair, folder_rel)
    )
    folder = _usable_folder(scope, pair, folder_rel)
    # Copy beside the target under a hidden name (never synced), then put it in place under
    # the first free name: a sync never sees a half-copied file, and nothing is overwritten.
    hidden = _copy_beside(src, folder)
    try:
        for candidate in restored_names(PurePosixPath(original).name):
            try:
                _place(hidden, folder / candidate)
            except FileExistsError:
                continue
            return join_rel(folder_rel, candidate), in_offloaded
        raise RecoveryError("Could not find a free name to restore it under.")
    finally:
        hidden.unlink(missing_ok=True)


def restore_entries(
    layout: Layout,
    scope: Scope,
    ids: list[str],
    *,
    offloaded: Mapping[int, Paths] | None = None,
) -> list[RestoreResult]:
    """Copy each entry back to where it came from. offloaded: each pair's open offload paths
    (store.open_paths); what goes back into an offloaded folder is marked local_only."""
    blocked = offloaded or {}
    results: list[RestoreResult] = []
    for entry_id in dict.fromkeys(ids):
        try:
            rel, local_only = _restore_one(layout, scope, entry_id, blocked)
            results.append(RestoreResult(entry_id, restored_to=rel, local_only=local_only))
        except (RecoveryError, FileOpError) as e:
            results.append(RestoreResult(entry_id, error=str(e)))
        except OSError as e:
            results.append(RestoreResult(entry_id, error=f"Could not copy it back: {e.strerror}"))
    return results
