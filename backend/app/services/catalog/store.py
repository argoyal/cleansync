import json
import logging
import os
import secrets
import threading
from pathlib import Path

from app.services.catalog.model import Catalog, CatalogEntry

log = logging.getLogger(__name__)


class CatalogStore:
    """app/data/catalog/<pair_id>.json, written atomically, read through a small cache.

    Safe to call from worker threads: the cache only ever swaps whole entries.
    """

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self._cache: dict[int, tuple[tuple[int, int], Catalog]] = {}
        self._bad: dict[int, tuple[int, int]] = {}  # unreadable files already logged
        self._lock = threading.RLock()

    def path(self, pair_id: int) -> Path:
        return self.dir / f"{pair_id}.json"

    def load(self, pair_id: int) -> Catalog | None:
        f = self.path(pair_id)
        try:
            st = f.stat()
        except FileNotFoundError:
            self._cache.pop(pair_id, None)
            return None
        key = (st.st_mtime_ns, st.st_size)
        hit = self._cache.get(pair_id)
        if hit is not None and hit[0] == key:
            return hit[1]
        try:
            catalog = Catalog.from_json(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            if self._bad.get(pair_id) != key:
                self._bad[pair_id] = key
                log.warning(
                    "the Drive listing for pair %s is unreadable; it will be rebuilt", pair_id
                )
            return None
        self._bad.pop(pair_id, None)
        self._cache[pair_id] = (key, catalog)
        return catalog

    def save(self, catalog: Catalog) -> None:
        with self._lock:
            self._save(catalog)

    def _save(self, catalog: Catalog) -> None:
        f = self.path(catalog.pair_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f".{f.name}.{secrets.token_hex(4)}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(catalog.to_json(), fh, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, f)
        finally:
            tmp.unlink(missing_ok=True)
        self._cache.pop(catalog.pair_id, None)

    def upsert(self, pair_id: int, entry: CatalogEntry) -> None:
        """Add one entry we know exists on Drive (e.g. a doc we just created)."""
        with self._lock:  # load -> modify -> save must not interleave with a refresh save
            current = self.load(pair_id)
            if current is None:
                return
            entries = [
                e
                for e in (*current.files.values(), *current.natives.values())
                if e.path != entry.path or bool(e.native) != bool(entry.native)
            ]
            entries.append(entry)
            self._save(Catalog.build(pair_id, current.refreshed_at, current.root_id, entries))

    def delete(self, pair_id: int) -> None:
        with self._lock:
            self.path(pair_id).unlink(missing_ok=True)
            self._cache.pop(pair_id, None)
            self._bad.pop(pair_id, None)
