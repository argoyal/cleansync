"""File actions from the UI on the local copy. Deleted items are moved into Recovery
(backups), never removed. Renames and moves are planned here (everything is checked before
anything moves); app.services.files.moves carries them out, on Drive too when needed.
"""

import os
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.pathsafe import safe_join
from app.models import SyncPair
from app.paths import Layout
from app.services.files.names import validate_name
from app.services.files.scope import (
    Scope,
    is_hidden,
    join_rel,
    pair_for,
    parent_rel,
    resolves_to_hidden,
    to_pair_rel,
    touches_pair_root,
)
from app.services.sync.backups import UNSYNCED


class FileOpError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


PAIR_FOLDER_MSG = (
    "This folder is a sync pair's folder or holds one. Change or remove the pair in "
    "Sync activity first."
)
CROSS_PAIR_MSG = (
    "Items can't be moved straight from one sync pair to another: Google Drive would see a "
    "delete in one and a new upload in the other. Move them on Google Drive instead."
)


def _existing(scope: Scope, rel: str) -> Path:
    if not rel or is_hidden(rel):
        raise FileOpError("not found", 404)
    path = safe_join(scope.root, rel)
    if not path.exists() or path.is_symlink() or resolves_to_hidden(scope.root, path.parent):
        raise FileOpError(f"'{rel}' was not found", 404)
    return path


def _folder(scope: Scope, rel: str) -> Path:
    if is_hidden(rel):
        raise FileOpError("folder not found", 404)
    path = safe_join(scope.root, rel)
    if not path.is_dir() or resolves_to_hidden(scope.root, path):
        raise FileOpError("folder not found", 404)
    return path


def make_folder(scope: Scope, parent: str, name: str) -> str:
    _folder(scope, parent)
    rel = join_rel(parent, validate_name(name))
    try:
        safe_join(scope.root, rel).mkdir()
    except FileExistsError:
        raise FileOpError("an item with that name is already here") from None
    return rel


@dataclass(frozen=True)
class MoveStep:
    """One rename or move, as account paths. pair is set when both ends are in that pair."""

    src: str
    dst: str
    pair: SyncPair | None

    @property
    def src_prel(self) -> str:
        assert self.pair is not None
        return to_pair_rel(self.pair, self.src)

    @property
    def dst_prel(self) -> str:
        assert self.pair is not None
        return to_pair_rel(self.pair, self.dst)


def _taken(src: Path, dest: Path) -> bool:
    # samefile: a case-only rename on a case-insensitive disk.
    return (dest.exists() or dest.is_symlink()) and not (
        dest.exists() and os.path.samefile(src, dest)
    )


def _step(scope: Scope, src_rel: str, dst_rel: str) -> MoveStep:
    src_pair = pair_for(scope.pairs, src_rel)
    dst_pair = pair_for(scope.pairs, dst_rel)
    if src_pair is not None and dst_pair is not None and src_pair.id != dst_pair.id:
        raise FileOpError(CROSS_PAIR_MSG, 422)
    # Into or out of a pair is a plain local move: the next sync uploads or deletes it.
    same = src_pair if src_pair is not None and dst_pair is not None else None
    return MoveStep(src_rel, dst_rel, same)


def plan_rename(scope: Scope, rel: str, new_name: str) -> MoveStep:
    if touches_pair_root(scope.pairs, rel):
        raise FileOpError(PAIR_FOLDER_MSG)
    src = _existing(scope, rel)
    new_rel = join_rel(parent_rel(rel), validate_name(new_name))
    if _taken(src, safe_join(scope.root, new_rel)):
        raise FileOpError("an item with that name is already here")
    return _step(scope, rel, new_rel)


def plan_move(scope: Scope, rels: list[str], to: str) -> list[MoveStep]:
    _folder(scope, to)
    plan: list[MoveStep] = []
    names: set[str] = set()
    for rel in rels:
        if touches_pair_root(scope.pairs, rel):
            raise FileOpError(PAIR_FOLDER_MSG)
        src = _existing(scope, rel)
        if to == rel or to.startswith(rel + "/"):
            raise FileOpError("a folder can't be moved into itself", 422)
        if parent_rel(rel) == to:
            continue  # already there
        new_rel = join_rel(to, src.name)
        if _taken(src, safe_join(scope.root, new_rel)) or src.name in names:
            raise FileOpError(f"'{src.name}' is already in that folder")
        names.add(src.name)
        plan.append(_step(scope, rel, new_rel))
    return plan


def apply_local(scope: Scope, step: MoveStep) -> None:
    """Move one planned item on disk; checks again that nothing took the name meanwhile."""
    src = safe_join(scope.root, step.src)
    dest = safe_join(scope.root, step.dst)
    if not src.exists() or src.is_symlink():
        raise FileOpError(f"'{step.src}' was not found", 404)
    if _taken(src, dest):
        raise FileOpError(f"'{dest.name}' is already in that folder")
    os.rename(src, dest)


def recovery_root(
    layout: Layout, scope: Scope, rel: str, when: datetime, run: str
) -> tuple[Path, str]:
    """Where a deleted item goes: backups/<email>/<pair id or 'unsynced'>/<date>/<run>/..."""
    pair = pair_for(scope.pairs, rel)
    bucket = str(pair.id) if pair else UNSYNCED
    inner = to_pair_rel(pair, rel) if pair else rel
    base = layout.backups_dir / scope.account.email / bucket / when.date().isoformat() / run
    return base, inner


def delete_to_recovery(layout: Layout, scope: Scope, rels: list[str], when: datetime) -> list[str]:
    """Move items into a new Recovery folder for this delete; returns where they went."""
    run = f".drivesync-deleted-{when:%H%M%S}-{secrets.token_hex(2)}"
    sources = []
    for rel in rels:
        if touches_pair_root(scope.pairs, rel):
            raise FileOpError(PAIR_FOLDER_MSG)
        sources.append((rel, _existing(scope, rel)))
    kept: list[str] = []
    for rel, src in sources:
        base, inner = recovery_root(layout, scope, rel, when, run)
        dest = safe_join(base, inner)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))  # a rename on the same disk, so instant
        kept.append(str(dest.relative_to(layout.backups_dir)))
    return kept
