import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conflict, SyncPair
from app.models.enums import ConflictState


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
