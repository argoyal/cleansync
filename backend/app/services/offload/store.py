"""Offload rows, and the questions the rest of CleanSync asks about them.

Paths here are pair paths (relative to the pair's own folder), like the filter file uses.
"""

import json
import os
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Offload
from app.models.enums import OffloadState as S
from app.paths import Layout

# Left out of sync (spec §6.5). VERIFYING and ATTENTION too: their Drive copy may already be in
# the trash, so letting bisync see the path again could delete the only copy left, on disk.
EXCLUDED_STATES = (S.PENDING, S.DELETING, S.VERIFYING, S.ACTIVE, S.ATTENTION)
# Not finished with: in force, or on its way back to Drive.
OPEN_STATES = (*EXCLUDED_STATES, S.RESTORING)
# A run was busy with these when CleanSync stopped.
UNFINISHED_STATES = (S.PENDING, S.DELETING, S.VERIFYING)
INTERRUPTED_NOTE = (
    "CleanSync stopped during this offload. The files are still on this server and stay out "
    "of sync. Use Restore to Drive to put back anything that left Google Drive."
)

# Nothing new goes into an offloaded folder (new files, folders, uploads, Recovery restores).
IN_OFFLOADED_MSG = (
    "This folder is offloaded: it's kept only on this server and doesn't sync with Google "
    "Drive. Restore it to Drive to add files here."
)

Paths = tuple[tuple[str, bool], ...]


def _paths(db: Session, pair_id: int, states: Iterable[str]) -> Paths:
    rows = db.execute(
        select(Offload.path, Offload.is_dir).where(
            Offload.pair_id == pair_id, Offload.state.in_(list(states))
        )
    ).all()
    return tuple(sorted((r.path, bool(r.is_dir)) for r in rows))


def excluded_paths(db: Session, pair_id: int) -> Paths:
    return _paths(db, pair_id, EXCLUDED_STATES)


def open_paths(db: Session, pair_id: int) -> Paths:
    return _paths(db, pair_id, OPEN_STATES)


def covers(paths: Iterable[tuple[str, bool]], prel: str) -> bool:
    """True when prel is an offloaded item, or inside an offloaded folder."""
    return any(prel == p or (is_dir and prel.startswith(p + "/")) for p, is_dir in paths)


def has_inside(paths: Iterable[tuple[str, bool]], prel: str) -> bool:
    """True when an offloaded item sits strictly inside the folder prel ("" = the pair folder)."""
    return any(p != prel and (prel == "" or p.startswith(prel + "/")) for p, _ in paths)


def recover_offloads(db: Session) -> int:
    """At startup: an offload a stop cut short needs a look. It stays out of sync."""
    rows = db.scalars(select(Offload).where(Offload.state.in_(list(UNFINISHED_STATES)))).all()
    for off in rows:
        off.state = S.ATTENTION
        off.note = INTERRUPTED_NOTE
    db.commit()
    return len(rows)


def manifest_file(layout: Layout, offload_id: int) -> Path:
    return layout.offloads_dir / f"{offload_id}.json"


def write_manifest(layout: Layout, offload_id: int, files: list[dict]) -> None:
    path = manifest_file(layout, offload_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(files), encoding="utf-8")
    os.replace(tmp, path)


def read_manifest(layout: Layout, offload_id: int) -> list[dict]:
    try:
        data = json.loads(manifest_file(layout, offload_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def delete_list_file(layout: Layout, job_id: int) -> Path:
    """Where an offload writes the checked list for one `rclone delete` (removed after it)."""
    return layout.offloads_dir / f"delete-{job_id}.txt"


def sweep_delete_lists(layout: Layout) -> int:
    """At startup: remove delete lists a hard stop left behind. They are never read again."""
    count = 0
    for path in layout.offloads_dir.glob("delete-*.txt"):
        if path.name[len("delete-") : -len(".txt")].isdigit():
            path.unlink(missing_ok=True)
            count += 1
    return count
