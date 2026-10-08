"""Per-item sync status for the browser (spec §8).

A PairIndex is built from one walk of the pair's folder compared with its Drive listing.
It is cached per pair and rebuilt when the listing changes, after a job or file action,
or after a minute (to notice edits made outside DriveSync). Open conflicts and the
"running" state are laid over it at request time, so they are always current.
"""

import asyncio
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Account, Conflict, SyncPair
from app.models.enums import ConflictState
from app.paths import Layout
from app.services.catalog.model import Catalog
from app.services.files.scope import HIDDEN_NAMES, is_hidden, parent_rel
from app.services.sync.pairs import pair_local_dir

# Least to most serious. "native", "offloaded" and "unsynced" never make a folder worse.
STATUS_ORDER = ("synced", "unknown", "upload", "download", "syncing", "conflict")
SEVERITY = {s: i for i, s in enumerate(STATUS_ORDER)}
MTIME_SLACK_S = 2.0


def worst(statuses: Iterable[str]) -> str:
    best = "synced"
    for s in statuses:
        if SEVERITY.get(s, -1) > SEVERITY[best]:
            best = s
    return best


@dataclass(frozen=True, slots=True)
class LocalEntry:
    size: int
    mtime: float
    is_dir: bool


@dataclass
class PairIndex:
    pair_id: int
    catalog_key: datetime | None  # refreshed_at of the listing it was compared with
    local: dict[str, LocalEntry]
    status: dict[str, str]  # only items that are not "synced"; pair paths
    dir_worst: dict[str, str]  # folder -> most serious status inside it ("" = pair folder)
    total_bytes: int

    @property
    def has_catalog(self) -> bool:
        return self.catalog_key is not None

    def item_status(self, rel: str) -> str:
        return self.status.get(rel, "synced" if self.has_catalog else "unknown")

    def folder_status(self, rel: str) -> str:
        return worst((self.item_status(rel), self.dir_worst.get(rel, "synced")))

    def overall(self) -> str:
        return self.folder_status("")


def _walk(local_dir: Path) -> dict[str, LocalEntry]:
    found: dict[str, LocalEntry] = {}
    stack = [("", local_dir)]
    while stack:
        rel_dir, folder = stack.pop()
        try:
            it = os.scandir(folder)
        except OSError:
            continue
        with it:
            for e in it:
                if e.name in HIDDEN_NAMES:
                    continue
                try:
                    if e.is_symlink():  # rclone skips links; so does DriveSync
                        continue
                    st = e.stat(follow_symlinks=False)
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
                found[rel] = LocalEntry(0 if is_dir else st.st_size, st.st_mtime, is_dir)
                if is_dir:
                    stack.append((rel, Path(e.path)))
    return found


def build_index(pair_id: int, local_dir: Path, catalog: Catalog | None) -> PairIndex:
    local = _walk(local_dir)
    status: dict[str, str] = {}
    if catalog is not None:
        files = catalog.files
        for rel, lf in local.items():
            ent = files.get(rel)
            if ent is None or ent.is_dir != lf.is_dir:
                status[rel] = "upload"
            elif not lf.is_dir:
                remote_ts = ent.mtime.timestamp() if ent.mtime else None
                same_size = ent.size == lf.size
                close = remote_ts is not None and abs(remote_ts - lf.mtime) <= MTIME_SLACK_S
                if not (same_size and close):
                    newer_here = remote_ts is None or lf.mtime > remote_ts
                    status[rel] = "upload" if newer_here else "download"
        for rel in files:
            if rel not in local and not is_hidden(rel):
                status[rel] = "download"
    dir_worst: dict[str, str] = {}
    for rel, st in status.items():
        parent = parent_rel(rel)
        while True:
            current = dir_worst.get(parent)
            # Every folder above an entry was raised when that entry was added, so stop here.
            if current is not None and SEVERITY[current] >= SEVERITY[st]:
                break
            dir_worst[parent] = st
            if parent == "":
                break
            parent = parent_rel(parent)
    total = sum(e.size for e in local.values())
    key = catalog.refreshed_at if catalog is not None else None
    return PairIndex(pair_id, key, local, status, dir_worst, total)


@dataclass
class Overlay:
    """Live facts laid over a cached index: open conflicts and a running sync."""

    conflicts: set[str] = field(default_factory=set)
    conflict_dirs: set[str] = field(default_factory=set)
    running: bool = False

    def apply(self, rel: str, status: str, is_dir: bool) -> str:
        if rel in self.conflicts or (is_dir and rel in self.conflict_dirs):
            return "conflict"
        if self.running and status in ("upload", "download"):
            return "syncing"
        return status


def load_overlay(db: Session, pair_id: int, running: bool) -> Overlay:
    rows = db.execute(
        select(Conflict.path, Conflict.local_variant, Conflict.remote_variant).where(
            Conflict.pair_id == pair_id, Conflict.state == ConflictState.OPEN
        )
    ).all()
    paths = {p for row in rows for p in row if p}
    dirs: set[str] = set()
    for p in paths:
        parent = parent_rel(p)
        while parent not in dirs:
            dirs.add(parent)
            if parent == "":
                break
            parent = parent_rel(parent)
    return Overlay(conflicts=paths, conflict_dirs=dirs, running=running)


class StatusIndexes:
    def __init__(
        self,
        *,
        layout: Layout,
        session_factory: sessionmaker[Session],
        catalog,
        ttl_s: float = 60.0,
        clock=time.monotonic,
    ) -> None:
        self.layout = layout
        self.session_factory = session_factory
        self.catalog = catalog
        self.ttl_s = ttl_s
        self.clock = clock
        self._cache: dict[int, tuple[float, int, PairIndex]] = {}
        self._gen: dict[int, int] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def invalidate(self, pair_id: int | None = None) -> None:
        """Forget cached statuses (one pair, or all). Safe from any thread."""
        ids = [pair_id] if pair_id is not None else list(self._gen)
        for pid in ids:
            self._gen[pid] = self._gen.get(pid, 0) + 1
            self._cache.pop(pid, None)

    def _fresh(self, pair_id: int, catalog_key: datetime | None) -> PairIndex | None:
        hit = self._cache.get(pair_id)
        if hit is None:
            return None
        built_at, gen, index = hit
        if gen != self._gen.get(pair_id, 0) or index.catalog_key != catalog_key:
            return None
        if self.clock() - built_at > self.ttl_s:
            return None
        return index

    async def get(self, pair: SyncPair, account: Account) -> PairIndex:
        catalog = await asyncio.to_thread(self.catalog.load, pair.id)
        key = catalog.refreshed_at if catalog is not None else None
        index = self._fresh(pair.id, key)
        if index is not None:
            return index
        lock = self._locks.setdefault(pair.id, asyncio.Lock())
        async with lock:
            index = self._fresh(pair.id, key)
            if index is not None:
                return index
            gen = self._gen.get(pair.id, 0)
            local_dir = pair_local_dir(self.layout, account, pair)
            index = await asyncio.to_thread(build_index, pair.id, local_dir, catalog)
            self._cache[pair.id] = (self.clock(), gen, index)
            return index
