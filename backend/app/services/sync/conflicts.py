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
    if 1 in found and 2 in found:
        return found[1], found[2]
    return None


def _stat(local_dir: Path, rel: str) -> tuple[int | None, datetime | None]:
    f = local_dir / rel
    if not rel or not f.is_file():
        return None, None
    st = f.stat()
    return st.st_size, datetime.fromtimestamp(st.st_mtime, UTC)


def record_conflicts(db: Session, pair: SyncPair, local_dir: Path, paths: list[str]) -> int:
    created = 0
    for path in paths:
        exists = db.scalar(
            select(Conflict).where(
                Conflict.pair_id == pair.id,
                Conflict.path == path,
                Conflict.state == ConflictState.OPEN,
            )
        )
        if exists:
            continue
        local_v, remote_v = find_variants(local_dir, path) or ("", "")
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
