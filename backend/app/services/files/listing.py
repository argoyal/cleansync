"""Merge one local folder with the pair's Drive listing into the items the browser shows."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.pathsafe import safe_join
from app.schemas.files import FileItemOut
from app.services.catalog.model import Catalog, CatalogEntry, google_url
from app.services.files.mimes import can_thumb, guess_mime
from app.services.files.scope import (
    Scope,
    is_hidden_name,
    join_rel,
    pair_for,
    pairs_inside,
    to_pair_rel,
)
from app.services.files.status import LocalEntry, Overlay, PairIndex, compare, worst


@dataclass
class PairView:
    index: PairIndex
    catalog: Catalog | None
    overlay: Overlay

    def status(self, prel: str, is_dir: bool) -> str:
        base = self.index.folder_status(prel) if is_dir else self.index.item_status(prel)
        return self.overlay.apply(prel, base, is_dir)


def make_item(
    *,
    path: str,
    name: str,
    is_dir: bool,
    size: int | None,
    mtime: datetime | None,
    status: str,
    local: bool,
    entry: CatalogEntry | None,
    pair_id: int | None,
    pair_root: bool = False,
) -> FileItemOut:
    listed = entry.mime if entry else None
    if is_dir:
        mime = None
    elif local:  # served from disk by its name, so its name decides
        mime = guess_mime(name) or listed
    else:
        mime = listed or guess_mime(name)
    return FileItemOut(
        name=name,
        path=path,
        is_dir=is_dir,
        size=None if is_dir else size,
        mtime=mtime,
        status=status,
        local=local,
        remote=entry is not None,
        mime=mime,
        native=entry.native if entry else None,
        google_url=google_url(entry) if entry else None,
        starred=bool(entry and entry.starred),
        pair_id=pair_id,
        thumb=local and not is_dir and can_thumb(mime, size),
        pair_root=pair_root,
    )


def remote_item(path: str, entry: CatalogEntry, status: str, pair_id: int) -> FileItemOut:
    """An item that is only on Drive: not downloaded yet, or a Google-native file."""
    return make_item(
        path=path,
        name=entry.display_name,
        is_dir=entry.is_dir,
        size=entry.size,
        mtime=entry.mtime,
        status="native" if entry.native else status,
        local=False,
        entry=entry,
        pair_id=pair_id,
    )


def _outside_status(scope: Scope, path: str, is_dir: bool, views: dict[int, PairView]) -> str:
    """A folder outside every pair shows the worst state of the pairs inside it. A pair's own
    folder, seen from its parent, counts as inside: it shows that pair's state."""
    if not is_dir:
        return "unsynced"
    inner = [p for p in scope.pairs if p.local_subpath == path] + pairs_inside(scope.pairs, path)
    if not inner:
        return "unsynced"
    return worst(
        views[p.id].overlay.apply("", views[p.id].index.overall(), True)
        for p in inner
        if p.id in views
    )


def list_folder(scope: Scope, rel: str, views: dict[int, PairView]) -> list[FileItemOut]:
    folder = safe_join(scope.root, rel)
    pair = pair_for(scope.pairs, rel)
    view = views.get(pair.id) if pair else None
    prel_dir = to_pair_rel(pair, rel) if pair else ""
    items: list[FileItemOut] = []
    local_names: set[tuple[str, bool]] = set()
    with os.scandir(folder) as it:
        for e in it:
            if is_hidden_name(e.name):
                continue
            try:
                if e.is_symlink():
                    continue
                st = e.stat(follow_symlinks=False)
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            local_names.add((e.name, is_dir))
            path = join_rel(rel, e.name)
            entry = None
            if pair is None:
                status = _outside_status(scope, path, is_dir, views)
            elif view is None:
                status = "unknown"
            else:
                prel = join_rel(prel_dir, e.name)
                entry = view.catalog.files.get(prel) if view.catalog else None
                if entry is not None and entry.is_dir != is_dir:
                    entry = None  # a folder and a file sharing a name are different items
                if prel in view.index.local:
                    status = view.status(prel, is_dir)
                else:  # appeared after the cached index was built: judge it now
                    size = 0 if is_dir else st.st_size
                    fresh = (
                        "unknown"
                        if view.catalog is None
                        else compare(LocalEntry(size, st.st_mtime, is_dir), entry)
                    )
                    status = view.overlay.apply(prel, fresh, is_dir)
            # A pair that covers the whole account has no folder of its own in any listing.
            pair_root = is_dir and any(p.local_subpath == path for p in scope.pairs)
            items.append(
                make_item(
                    path=path,
                    name=e.name,
                    is_dir=is_dir,
                    size=st.st_size,
                    mtime=datetime.fromtimestamp(st.st_mtime, UTC),
                    status=status,
                    local=True,
                    entry=entry,
                    pair_id=pair.id if pair else None,
                    pair_root=pair_root,
                )
            )
    if pair is not None and view is not None and view.catalog is not None:
        for ent in view.catalog.children(prel_dir):
            if is_hidden_name(ent.name) or (
                not ent.native and (ent.name, ent.is_dir) in local_names
            ):
                continue
            prel = join_rel(prel_dir, ent.name)
            items.append(
                remote_item(join_rel(rel, ent.name), ent, view.status(prel, ent.is_dir), pair.id)
            )
    items.sort(key=lambda i: (not i.is_dir, i.name.casefold(), i.path))
    return items
