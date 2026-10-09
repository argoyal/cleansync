"""What an offload would do, item by item (spec §7.2 step 1, §7.2a). No rclone, no Drive:
only the disk, the database rows passed in, and the cached Drive listing."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.core.pathsafe import UnsafePath, safe_join
from app.models import SyncPair
from app.models.enums import Direction
from app.services.catalog.model import Catalog
from app.services.files.ops import prune_selection
from app.services.files.scope import (
    Scope,
    is_hidden_name,
    join_rel,
    pair_for,
    resolves_to_hidden,
    to_account_rel,
    to_pair_rel,
    touches_pair_root,
)
from app.services.files.status import has_pending_changes
from app.services.offload import store
from app.services.sync.engine import WAITING_FOR_USER

NATIVE_MSG = "Stays on Drive — not a file DriveSync can keep a copy of yet."
NOT_SYNCED_MSG = "This isn't in a synced folder, so it isn't on Google Drive."
DRIVE_ONLY_MSG = "This is only on Google Drive so far; there is no copy on this server to keep."
NOT_FOUND_MSG = "This is no longer here."
PAIR_ROOT_MSG = (
    "This is a synced folder itself (or holds one). Offload the files and folders inside it."
)
ONE_WAY_MSG = "Offload works only in folders that sync both ways."
NOT_STARTED_MSG = "This folder hasn't had its first sync yet."
RECONNECT_MSG = "Reconnect this Google account first (Settings, Accounts)."
ALREADY_MSG = "This is already offloaded."
PART_MSG = (
    "Part of this folder is already offloaded. Restore that part first, or offload the other "
    "items one by one."
)
CONFLICT_MSG = "This has an open conflict. Resolve it in Conflicts first."
BUSY_MSG = "This folder is syncing right now. Try again when the sync finishes."
PENDING_MSG = "This has changes waiting to sync. Sync now, then try again."
EMPTY_MSG = "Nothing here to offload: it has no files."
ONLY_NATIVE_MSG = "Nothing here to offload: everything in it stays on Drive."
WAITING_MSG = (
    "Syncing of this folder stopped and is waiting for you to look at it (see Sync activity). "
    "Deal with that first, then try again."
)


@dataclass
class PlanItem:
    path: str  # account path, as picked
    pair: SyncPair | None = None
    prel: str = ""  # path inside the pair
    is_dir: bool = False
    file_count: int = 0
    total_bytes: int = 0
    stays_on_drive: list[str] = field(default_factory=list)  # account paths of native docs
    blocked: str | None = None


def local_files(path: Path) -> tuple[int, int]:
    """(files, bytes) the way rclone sees them: no links, none of DriveSync's own files."""
    if path.is_file():
        return 1, path.stat().st_size
    count = size = 0
    for dirpath, dirnames, filenames in os.walk(path):  # never follows folder links
        dirnames[:] = [d for d in dirnames if not is_hidden_name(d)]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if is_hidden_name(name) or os.path.islink(full):
                continue
            count += 1
            size += os.stat(full).st_size
    return count, size


def _under(rel: str, prel: str) -> bool:
    return rel == prel or rel.startswith(prel + "/")


def plan_offload(
    scope: Scope,
    rels: list[str],
    *,
    catalogs: dict[int, Catalog | None],
    offloads: dict[int, store.Paths],
    conflicts: dict[int, set[str]],
    busy: set[int],
    connected: bool,
) -> list[PlanItem]:
    return [
        _plan(scope, rel, catalogs, offloads, conflicts, busy, connected)
        for rel in prune_selection(rels)
    ]


def _plan(scope, rel, catalogs, offloads, conflicts, busy, connected) -> PlanItem:
    item = PlanItem(path=rel)
    pair = pair_for(scope.pairs, rel)
    if pair is None:
        item.blocked = NOT_SYNCED_MSG
        return item
    item.pair, item.prel = pair, to_pair_rel(pair, rel)
    catalog = catalogs.get(pair.id)
    try:
        path = safe_join(scope.root, rel)
    except UnsafePath:
        item.blocked = NOT_FOUND_MSG
        return item
    here = path.exists() and not path.is_symlink() and not resolves_to_hidden(scope.root, path)
    if not here:
        if catalog is not None and item.prel in catalog.natives:
            item.blocked, item.stays_on_drive = NATIVE_MSG, [rel]
        elif catalog is not None and item.prel in catalog.files:
            item.blocked = DRIVE_ONLY_MSG
        else:
            item.blocked = NOT_FOUND_MSG
        return item
    item.is_dir = path.is_dir()
    prel = item.prel
    paths = offloads.get(pair.id, ())
    open_conflicts = conflicts.get(pair.id, set())
    if touches_pair_root(scope.pairs, rel):
        item.blocked = PAIR_ROOT_MSG
    elif pair.direction != Direction.BIDIRECTIONAL:
        item.blocked = ONE_WAY_MSG
    elif not pair.initialized:
        item.blocked = NOT_STARTED_MSG
    elif not connected:
        item.blocked = RECONNECT_MSG
    elif pair.attention_reason in WAITING_FOR_USER:
        item.blocked = WAITING_MSG
    elif store.covers(paths, prel):
        item.blocked = ALREADY_MSG
    elif item.is_dir and store.has_inside(paths, prel):
        item.blocked = PART_MSG
    elif any(_under(c, prel) for c in open_conflicts):
        item.blocked = CONFLICT_MSG
    elif pair.id in busy:
        item.blocked = BUSY_MSG
    if item.blocked:
        return item
    if item.is_dir and catalog is not None:
        item.stays_on_drive = sorted(
            to_account_rel(pair, join_rel(e.parent, e.display_name))
            for e in catalog.natives.values()
            if e.path.startswith(prel + "/")
        )
    item.file_count, item.total_bytes = local_files(path)
    if item.file_count == 0:
        item.blocked = ONLY_NATIVE_MSG if item.stays_on_drive else EMPTY_MSG
    elif has_pending_changes(path, prel, catalog, open_conflicts):
        item.blocked = PENDING_MSG
    return item
