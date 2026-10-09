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
from app.models.enums import Direction
from app.paths import Layout
from app.services.files.names import free_name, validate_name
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


UNDO_FAILED = (
    "Google Drive was renamed already and could not be changed back; the next sync will "
    "reconcile it."
)


class UndoFailed(FileOpError):
    """The disk move failed after the Drive move, and the Drive move could not be undone."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(f"{cause} {UNDO_FAILED}", getattr(cause, "status", 500))
        self.cause = cause


class PartialError(FileOpError):
    """A multi-item action stopped part-way: some items were done, one failed."""

    def __init__(
        self, cause: Exception, *, done: list[str], failed: list[str], not_done: list[str]
    ) -> None:
        super().__init__(str(cause), getattr(cause, "status", 500))
        self.cause = cause
        self.done = done
        self.failed = failed
        self.not_done = not_done


PAIR_FOLDER_MSG = (
    "This folder is a sync pair's folder or holds one. Change or remove the pair in "
    "Sync activity first."
)
CROSS_PAIR_MSG = (
    "Items can't be moved straight from one sync pair to another: Google Drive would see a "
    "delete in one and a new upload in the other. Move them on Google Drive instead."
)

ONE_WAY_MSG = (
    "This folder only receives changes from Google Drive; files added or changed here would "
    "be removed by the next sync."
)


def refuse_one_way(scope: Scope, rels: list[str]) -> None:
    """A Drive-to-here pair is mirrored with a one-way sync that removes local extras and undoes
    local edits, so nothing may be added, changed or removed inside it from the UI."""
    for rel in rels:
        pair = pair_for(scope.pairs, rel)
        if pair is not None and pair.direction == Direction.REMOTE_TO_LOCAL:
            raise FileOpError(ONE_WAY_MSG, 409)


def prune_selection(rels: list[str]) -> list[str]:
    """Drop repeated paths and paths inside another selected folder (they go along with it)."""
    unique = list(dict.fromkeys(rels))
    chosen = set(unique)
    out = []
    for rel in unique:
        parent = rel
        inside = False
        while "/" in parent:
            parent = parent_rel(parent)
            if parent in chosen:
                inside = True
                break
        if not inside and not (rel and "" in chosen):
            out.append(rel)
    return out


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


NOT_SYNCED_MSG = "This folder isn't synced with Google Drive. Open a synced folder to add files."


def require_synced(scope: Scope, folder_rel: str) -> None:
    """New items only go into synced folders (spec §8): anywhere else they would never reach
    Google Drive."""
    if pair_for(scope.pairs, folder_rel) is None:
        raise FileOpError(NOT_SYNCED_MSG, 409)


def make_folder(scope: Scope, parent: str, name: str) -> str:
    refuse_one_way(scope, [parent])
    _folder(scope, parent)
    require_synced(scope, parent)
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
    refuse_one_way(scope, [rel])
    if touches_pair_root(scope.pairs, rel):
        raise FileOpError(PAIR_FOLDER_MSG)
    src = _existing(scope, rel)
    new_rel = join_rel(parent_rel(rel), validate_name(new_name))
    if _taken(src, safe_join(scope.root, new_rel)):
        raise FileOpError("an item with that name is already here")
    return _step(scope, rel, new_rel)


def plan_move(scope: Scope, rels: list[str], to: str) -> list[MoveStep]:
    refuse_one_way(scope, [*rels, to])
    _folder(scope, to)
    plan: list[MoveStep] = []
    names: set[str] = set()
    for rel in prune_selection(rels):
        if touches_pair_root(scope.pairs, rel):
            raise FileOpError(PAIR_FOLDER_MSG)
        src = _existing(scope, rel)
        folded, to_folded = rel.casefold(), to.casefold()
        if to_folded == folded or to_folded.startswith(folded + "/"):
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


def recovery_root(layout: Layout, scope: Scope, rel: str, when: datetime) -> tuple[Path, str]:
    """Where a deleted item goes: backups/<email>/<pair id or 'unsynced'>/<date>/, then a new
    .drivesync-deleted-<HHMMSS>-<hex>/ folder per delete, then the path inside the pair."""
    pair = pair_for(scope.pairs, rel)
    bucket = str(pair.id) if pair else UNSYNCED
    inner = to_pair_rel(pair, rel) if pair else rel
    return layout.backups_dir / scope.account.email / bucket / when.date().isoformat(), inner


def _new_run_folder(base_day: Path, when: datetime) -> Path:
    """A fresh Recovery folder for this delete; never reuses one that already exists."""
    base_day.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        run = base_day / f".drivesync-deleted-{when:%H%M%S}-{secrets.token_hex(2)}"
        try:
            run.mkdir()
            return run
        except FileExistsError:
            continue
    raise FileOpError("could not create a new Recovery folder", 500)


def delete_to_recovery(layout: Layout, scope: Scope, rels: list[str], when: datetime) -> list[str]:
    """Move items into a new Recovery folder for this delete; returns where they went.

    With several items, a failure part-way raises PartialError naming what was done.
    """
    refuse_one_way(scope, rels)
    sources = []
    for rel in prune_selection(rels):
        if touches_pair_root(scope.pairs, rel):
            raise FileOpError(PAIR_FOLDER_MSG)
        sources.append((rel, _existing(scope, rel)))
    runs: dict[Path, Path] = {}  # backups/<email>/<bucket>/<date> -> this delete's folder
    kept: list[str] = []
    for n, (rel, src) in enumerate(sources):
        try:
            day, inner = recovery_root(layout, scope, rel, when)
            if day not in runs:
                runs[day] = _new_run_folder(day, when)
            dest = safe_join(runs[day], inner)
            dest.parent.mkdir(parents=True, exist_ok=True)
            # A rename when backups are on the same disk as storage; otherwise copy, then remove.
            shutil.move(str(src), str(dest))
        except Exception as e:
            if len(sources) == 1:
                raise
            raise PartialError(
                e,
                done=[r for r, _ in sources[:n]],
                failed=[rel],
                not_done=[r for r, _ in sources[n + 1 :]],
            ) from e
        kept.append(str(dest.relative_to(layout.backups_dir)))
    return kept


OFFICE_DEFAULT_NAMES = {
    "docx": "Untitled document",
    "xlsx": "Untitled spreadsheet",
    "pptx": "Untitled presentation",
}


def require_folder(scope: Scope, rel: str) -> Path:
    """The folder on disk, or 404 when it is missing, hidden or reached through a symlink."""
    return _folder(scope, rel)


def new_office_file(
    scope: Scope, templates: Path, folder_rel: str, kind: str, name: str | None
) -> str:
    """Copy a generated blank Office file into the folder; the next sync uploads it."""
    from app.services.files.upload import _copy_beside, _place  # upload imports this module

    refuse_one_way(scope, [folder_rel])
    template = templates / f"blank.{kind}"
    if not template.is_file():
        raise FileOpError("the blank Office files are missing from this installation", 500)
    folder = _folder(scope, folder_rel)
    require_synced(scope, folder_rel)
    base = validate_name(name) if name else OFFICE_DEFAULT_NAMES[kind]
    if not base.lower().endswith(f".{kind}"):
        base = validate_name(f"{base}.{kind}")
    # Copy to a hidden file beside the target, then place that: never a link to the template,
    # and never over something already there.
    hidden = _copy_beside(template, folder)
    try:
        os.utime(hidden)  # a new file: modified now, not when the template was made
        for _ in range(1000):
            final = free_name(folder, base)
            try:
                _place(hidden, folder / final)
            except FileExistsError:
                continue  # someone took that name a moment ago
            return join_rel(folder_rel, final)
        raise FileOpError("could not find a free name", 409)
    finally:
        hidden.unlink(missing_ok=True)
