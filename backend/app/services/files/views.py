"""Recent, Starred and Search: lists across every pair of one account."""

import heapq
from datetime import UTC, datetime

from app.schemas.files import FileItemOut
from app.services.files.listing import PairView, make_item, remote_item
from app.services.files.scope import Scope, is_hidden, to_account_rel

MAX_SEARCH_RESULTS = 200
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _local_item(scope: Scope, pair, view: PairView, prel: str) -> FileItemOut:
    lf = view.index.local[prel]
    entry = view.catalog.files.get(prel) if view.catalog else None
    return make_item(
        path=to_account_rel(pair, prel),
        name=prel.rsplit("/", 1)[-1],
        is_dir=lf.is_dir,
        size=lf.size,
        mtime=datetime.fromtimestamp(lf.mtime, UTC),
        status=view.status(prel, lf.is_dir),
        local=True,
        entry=entry,
        pair_id=pair.id,
    )


def _drive_only(view: PairView):
    """Listing entries with no local copy (not downloaded yet, or Google-native)."""
    if view.catalog is None:
        return
    for ent in view.catalog.natives.values():
        yield ent
    for path, ent in view.catalog.files.items():
        if path not in view.index.local and not is_hidden(path):
            yield ent


def recent(scope: Scope, views: dict[int, PairView], limit: int) -> list[FileItemOut]:
    candidates: list[tuple[datetime, FileItemOut]] = []
    for pair in scope.pairs:
        view = views.get(pair.id)
        if view is None:
            continue
        files = ((p, e) for p, e in view.index.local.items() if not e.is_dir)
        for prel, _ in heapq.nlargest(limit, files, key=lambda pe: pe[1].mtime):
            item = _local_item(scope, pair, view, prel)
            candidates.append((item.mtime or _EPOCH, item))
        remote = (e for e in _drive_only(view) if not e.is_dir)
        for ent in heapq.nlargest(limit, remote, key=lambda e: e.mtime or _EPOCH):
            prel = ent.path
            item = remote_item(to_account_rel(pair, prel), ent, view.status(prel, False), pair.id)
            candidates.append((ent.mtime or _EPOCH, item))
    candidates.sort(key=lambda c: c[0], reverse=True)
    return [item for _, item in candidates[:limit]]


def starred(scope: Scope, views: dict[int, PairView]) -> list[FileItemOut]:
    out: list[FileItemOut] = []
    for pair in scope.pairs:
        view = views.get(pair.id)
        if view is None or view.catalog is None:
            continue
        for ent in (*view.catalog.files.values(), *view.catalog.natives.values()):
            if not ent.starred or is_hidden(ent.path):
                continue
            if not ent.native and ent.path in view.index.local:
                out.append(_local_item(scope, pair, view, ent.path))
            else:
                status = view.status(ent.path, ent.is_dir)
                out.append(remote_item(to_account_rel(pair, ent.path), ent, status, pair.id))
    out.sort(key=lambda i: (not i.is_dir, i.name.casefold()))
    return out


def search(
    scope: Scope, views: dict[int, PairView], query: str, limit: int = MAX_SEARCH_RESULTS
) -> list[FileItemOut]:
    needle = query.casefold()
    out: list[FileItemOut] = []
    for pair in scope.pairs:
        view = views.get(pair.id)
        if view is None:
            continue
        for prel in view.index.local:
            if len(out) >= limit:
                break
            if needle in prel.rsplit("/", 1)[-1].casefold():
                out.append(_local_item(scope, pair, view, prel))
        for ent in _drive_only(view):
            if len(out) >= limit:
                break
            if needle in ent.display_name.casefold():
                status = view.status(ent.path, ent.is_dir)
                out.append(remote_item(to_account_rel(pair, ent.path), ent, status, pair.id))
    out.sort(key=lambda i: (not i.is_dir, i.name.casefold(), i.path))
    return out[:limit]
