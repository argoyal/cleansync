import os
import re
import shutil
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.pathsafe import UnsafePath, safe_join
from app.models import Account, Conflict, SyncPair
from app.models.enums import AttentionReason, ConflictState, PairStatus
from app.paths import Layout
from app.services.sync.pairs import pair_local_dir


def _rel(parent: PurePosixPath, name: str) -> str:
    return name if str(parent) in ("", ".") else f"{parent}/{name}"


def find_variants(local_dir: Path, rel: str) -> tuple[str, str] | None:
    """The newest complete pair of conflict copies on disk: (conflict2k-1, conflict2k)."""
    p = PurePosixPath(rel)
    folder = local_dir / p.parent
    if not folder.is_dir():
        return None
    patterns = [
        re.compile(rf"^{re.escape(p.stem)}\.conflict(\d+){re.escape(p.suffix)}$"),
        re.compile(rf"^{re.escape(p.name)}\.conflict(\d+)$"),
    ]
    found: dict[int, str] = {}
    for entry in folder.iterdir():
        for pat in patterns:
            m = pat.match(entry.name)
            if m:
                found.setdefault(int(m.group(1)), _rel(p.parent, entry.name))
    pairs = [n for n in found if n % 2 == 0 and n - 1 in found]
    if not pairs:
        return None
    k = max(pairs)
    return found[k - 1], found[k]


def _stat(local_dir: Path, rel: str) -> tuple[int | None, datetime | None]:
    f = local_dir / rel
    if not rel or not f.is_file():
        return None, None
    st = f.stat()
    return st.st_size, datetime.fromtimestamp(st.st_mtime, UTC)


def _variants(
    local_dir: Path, path: str, named: dict[str, tuple[str | None, str | None]]
) -> tuple[str, str]:
    local_v, remote_v = named.get(path, (None, None))
    if local_v and remote_v:
        return local_v, remote_v
    return find_variants(local_dir, path) or ("", "")


def record_conflicts(
    db: Session,
    pair: SyncPair,
    local_dir: Path,
    paths: list[str],
    variants: dict[str, tuple[str | None, str | None]] | None = None,
    *,
    require_files: bool = False,
) -> int:
    """Add a row per new conflict; returns how many rows were added.

    Variant names come from rclone's own rename lines when known, else the newest pair on disk.
    With require_files, a conflict is only recorded when both copies exist on disk.
    """
    named = variants or {}
    created = 0
    for path in dict.fromkeys(paths):
        local_v, remote_v = _variants(local_dir, path, named)
        if require_files and not (
            local_v
            and remote_v
            and (local_dir / local_v).is_file()
            and (local_dir / remote_v).is_file()
        ):
            continue
        exists = db.scalar(
            select(Conflict.id).where(
                Conflict.pair_id == pair.id,
                Conflict.path == path,
                Conflict.local_variant == local_v,
                Conflict.state == ConflictState.OPEN,
            )
        )
        if exists:
            continue
        ls, lm = _stat(local_dir, local_v)
        rs, rm = _stat(local_dir, remote_v)
        db.add(
            Conflict(
                pair_id=pair.id,
                path=path,
                local_variant=local_v,
                remote_variant=remote_v,
                local_size=ls,
                local_mtime=lm,
                remote_size=rs,
                remote_mtime=rm,
            )
        )
        created += 1
    db.commit()
    return created


ACTIONS = ("keep_local", "keep_remote", "keep_both", "skip")


class ConflictError(ValueError):
    pass


def _free_name(folder: Path, stem: str, suffix: str) -> Path:
    candidate = folder / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem} {n}{suffix}"
        n += 1
    return candidate


def _to_backup(src: Path, backup_root: Path, rel: str) -> None:
    dst = safe_join(backup_root, rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():  # never overwrite an earlier backup (e.g. a second resolve the same day)
        dst = _free_name(dst.parent, PurePosixPath(dst.name).stem, PurePosixPath(dst.name).suffix)
    shutil.move(str(src), str(dst))


def resolve_conflict(
    db: Session,
    layout: Layout,
    account: Account,
    pair: SyncPair,
    conflict: Conflict,
    action: str,
    today: date,
) -> Conflict:
    if action not in ACTIONS:
        raise ConflictError(f"unknown action {action!r}")
    if conflict.state != ConflictState.OPEN:
        raise ConflictError("this conflict is already handled")
    local_dir = pair_local_dir(layout, account, pair)
    backup_root = (
        layout.backups_dir / account.email / str(pair.id) / today.isoformat() / "conflicts"
    )
    if action != "skip":
        try:
            _resolve_files(local_dir, backup_root, conflict, action)
        except UnsafePath as e:
            raise ConflictError(f"unsafe path in conflict: {e}") from e
    conflict.state = ConflictState.SKIPPED if action == "skip" else ConflictState.RESOLVED
    conflict.resolution = action
    conflict.resolved_at = utcnow()
    db.commit()
    still_open = db.scalar(
        select(Conflict.id).where(Conflict.pair_id == pair.id, Conflict.state == ConflictState.OPEN)
    )
    if still_open is None and pair.attention_reason == AttentionReason.CONFLICTS:
        pair.status = PairStatus.PAUSED if pair.paused else PairStatus.HEALTHY
        pair.attention_reason = None
        db.commit()
    return conflict


def _resolve_files(local_dir: Path, backup_root: Path, conflict: Conflict, action: str) -> None:
    if not conflict.local_variant or not conflict.remote_variant:
        raise ConflictError("CleanSync could not find both versions; run a sync first")
    local_v = safe_join(local_dir, conflict.local_variant)
    remote_v = safe_join(local_dir, conflict.remote_variant)
    if not local_v.is_file() or not remote_v.is_file():
        raise ConflictError("the conflict files are no longer there; run a sync first")
    original = safe_join(local_dir, conflict.path)
    if action in ("keep_local", "keep_remote"):
        if action == "keep_local":
            winner, loser, loser_rel = local_v, remote_v, conflict.remote_variant
        else:
            winner, loser, loser_rel = remote_v, local_v, conflict.local_variant
        if original.exists():
            _to_backup(original, backup_root, conflict.path)
        _to_backup(loser, backup_root, loser_rel)
        os.replace(winner, original)
    else:
        p = PurePosixPath(conflict.path)
        os.replace(local_v, _free_name(original.parent, f"{p.stem} (local)", p.suffix))
        os.replace(remote_v, _free_name(original.parent, f"{p.stem} (Drive)", p.suffix))
