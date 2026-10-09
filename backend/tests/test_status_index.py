import os
from datetime import UTC, datetime, timedelta

from app.services.catalog.model import Catalog, CatalogEntry
from app.services.files.status import Overlay, build_index, worst

NOW = datetime(2026, 10, 8, 8, 0, tzinfo=UTC)


def _file(root, rel, data=b"abc", when=NOW):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.utime(p, (when.timestamp(), when.timestamp()))
    return p


def _cat(*entries, when=NOW):
    return Catalog.build(1, when, "root", list(entries))


def _e(path, size=3, when=NOW, is_dir=False, **kw):
    return CatalogEntry(path, is_dir, size, when, **kw)


def test_same_file_on_both_sides_is_synced(tmp_path):
    _file(tmp_path, "Work/a.txt")
    idx = build_index(1, tmp_path, _cat(_e("Work", 0, is_dir=True), _e("Work/a.txt")))
    assert idx.item_status("Work/a.txt") == "synced"
    assert idx.folder_status("Work") == "synced"
    assert idx.overall() == "synced"


def test_local_only_waits_to_upload_and_drive_only_waits_to_download(tmp_path):
    _file(tmp_path, "Work/new.txt")
    idx = build_index(
        1,
        tmp_path,
        _cat(_e("Work", 0, is_dir=True), _e("Work/from-drive.txt"), _e("Photos", 0, is_dir=True)),
    )
    assert idx.item_status("Work/new.txt") == "upload"
    assert idx.item_status("Work/from-drive.txt") == "download"
    assert idx.folder_status("Photos") == "download"
    assert idx.folder_status("Work") == "download"  # download is more serious than upload
    assert idx.overall() == "download"


def test_changed_file_follows_the_newer_side(tmp_path):
    _file(tmp_path, "mine.txt", b"longer text", when=NOW + timedelta(minutes=5))
    _file(tmp_path, "theirs.txt", b"x", when=NOW)
    _file(tmp_path, "touched.txt", b"abc", when=NOW + timedelta(seconds=1))
    idx = build_index(
        1,
        tmp_path,
        _cat(
            _e("mine.txt", 3), _e("theirs.txt", 9, when=NOW + timedelta(hours=1)), _e("touched.txt")
        ),
    )
    assert idx.item_status("mine.txt") == "upload"
    assert idx.item_status("theirs.txt") == "download"
    assert idx.item_status("touched.txt") == "synced"  # within the 2 s clock slack


def test_hidden_files_and_links_are_ignored(tmp_path):
    _file(tmp_path, "RCLONE_TEST")
    _file(tmp_path, ".google-exports/Doc.docx")
    (tmp_path / "link").symlink_to(tmp_path / "RCLONE_TEST")
    idx = build_index(1, tmp_path, _cat(_e("RCLONE_TEST")))
    assert idx.local == {}
    assert idx.status == {}


def test_without_a_listing_everything_is_unknown(tmp_path):
    _file(tmp_path, "a/b.txt")
    idx = build_index(1, tmp_path, None)
    assert idx.item_status("a/b.txt") == "unknown"
    assert idx.folder_status("a") == "unknown"
    assert idx.total_bytes == 3


def test_missing_local_folder_lists_drive_items_as_downloads(tmp_path):
    idx = build_index(1, tmp_path / "missing", _cat(_e("a.txt")))
    assert idx.item_status("a.txt") == "download"


def test_overlay_conflicts_and_running():
    ov = Overlay(conflicts={"W/a.conflict1.txt"}, conflict_dirs={"W", ""}, running=True)
    assert ov.apply("W/a.conflict1.txt", "synced", False) == "conflict"
    assert ov.apply("W", "upload", True) == "conflict"
    assert ov.apply("X/b.txt", "upload", False) == "syncing"
    assert ov.apply("X/c.txt", "synced", False) == "synced"


def test_worst_ignores_statuses_that_never_make_a_folder_worse():
    assert worst(["synced", "native", "unsynced"]) == "synced"
    assert worst(["upload", "conflict", "download"]) == "conflict"


# ---- StatusIndexes -------------------------------------------------------------------------
import asyncio  # noqa: E402
import threading  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from app.services.files import status as status_mod  # noqa: E402
from app.services.files.status import StatusIndexes  # noqa: E402


class _Cat:
    def __init__(self, cat):
        self.cat = cat

    def load(self, pair_id):
        return self.cat


def _indexes(monkeypatch, tmp_path, cat):
    monkeypatch.setattr(status_mod, "pair_local_dir", lambda layout, account, pair: tmp_path)
    return StatusIndexes(layout=None, catalog=_Cat(cat))


def test_invalidate_all_marks_every_cached_pair_stale(monkeypatch, tmp_path):
    cat = _cat(_e("a.txt"))
    idx = _indexes(monkeypatch, tmp_path, cat)
    account = SimpleNamespace()

    async def run():
        for pid in (1, 2):
            await idx.get(SimpleNamespace(id=pid), account)
        assert all(idx._fresh(pid, cat.refreshed_at) for pid in (1, 2))
        idx.invalidate()
        assert all(idx._fresh(pid, cat.refreshed_at) is None for pid in (1, 2))

    asyncio.run(run())


def test_stale_index_is_served_while_one_rebuild_runs(monkeypatch, tmp_path):
    cat = _cat(_e("a.txt"))
    idx = _indexes(monkeypatch, tmp_path, cat)
    pair, account = SimpleNamespace(id=1), SimpleNamespace()
    gate, calls = threading.Event(), []
    real = status_mod.build_index

    def slow(*a):
        calls.append(1)
        if len(calls) > 1:
            gate.wait(5)
        return real(*a)

    monkeypatch.setattr(status_mod, "build_index", slow)

    async def run():
        old, _ = await idx.get(pair, account)
        idx.invalidate()
        got = await asyncio.gather(*(idx.get(pair, account) for _ in range(5)))
        assert all(g[0] is old for g in got)  # served at once, from the old index
        await asyncio.sleep(0.05)
        assert len(calls) == 2  # one rebuild, not five
        gate.set()
        await asyncio.gather(*idx._building.values())
        new, _ = await idx.get(pair, account)
        assert new is not old and len(calls) == 2

    asyncio.run(run())


def test_tabs_are_told_when_a_rebuild_finishes_after_serving_old_statuses(monkeypatch, tmp_path):
    cat = _cat(_e("a.txt"))
    idx = _indexes(monkeypatch, tmp_path, cat)
    told = []
    idx.on_refreshed = lambda pair: told.append(pair.id)
    pair, account = SimpleNamespace(id=1, account_id=5), SimpleNamespace()

    async def run():
        await idx.get(pair, account)  # first build: nobody saw old statuses
        assert told == []
        idx.invalidate()
        await idx.get(pair, account)  # served the old index; a rebuild starts
        await asyncio.gather(*idx._building.values())
        assert told == [1]  # now a tab can fetch the new statuses
        await idx.get(pair, account)
        assert told == [1]

    asyncio.run(run())


def test_forget_also_clears_the_served_stale_mark(monkeypatch, tmp_path):
    idx = _indexes(monkeypatch, tmp_path, _cat(_e("a.txt")))
    idx._served_stale.add(1)
    idx.forget(1)
    assert 1 not in idx._served_stale


def test_concurrent_first_requests_share_one_build(monkeypatch, tmp_path):
    idx = _indexes(monkeypatch, tmp_path, _cat(_e("a.txt")))
    calls = []
    real = status_mod.build_index
    monkeypatch.setattr(status_mod, "build_index", lambda *a: (calls.append(1), real(*a))[1])

    async def run():
        pair, account = SimpleNamespace(id=1), SimpleNamespace()
        await asyncio.gather(*(idx.get(pair, account) for _ in range(5)))

    asyncio.run(run())
    assert len(calls) == 1


def test_forget_drops_a_deleted_pair(monkeypatch, tmp_path):
    idx = _indexes(monkeypatch, tmp_path, None)
    asyncio.run(idx.get(SimpleNamespace(id=1), SimpleNamespace()))
    idx.invalidate(1)
    idx.forget(1)
    assert 1 not in idx._cache and 1 not in idx._gen


def test_hidden_names_ignore_case_in_the_walk(tmp_path):
    _file(tmp_path, ".Google-Exports/a.docx")
    _file(tmp_path, "rclone_test")
    assert build_index(1, tmp_path, None).local == {}


def test_offloaded_paths_have_no_sync_status_and_never_raise_their_folder(tmp_path):
    _file(tmp_path, "Work/Old/a.txt")
    _file(tmp_path, "Work/b.txt")
    catalog = _cat(_e("Work", 0, is_dir=True), _e("Work/b.txt"))
    plain = build_index(1, tmp_path, catalog)
    assert plain.folder_status("Work") == "upload"  # without the offload: waits to upload
    idx = build_index(1, tmp_path, catalog, excluded=(("Work/Old", True),))
    assert idx.folder_status("Work") == "synced"
    assert "Work/Old/a.txt" in idx.local and idx.excluded == (("Work/Old", True),)
    overlay = Overlay(offloaded=(("Work/Old", True),))
    assert overlay.apply("Work/Old", idx.folder_status("Work/Old"), True) == "offloaded"
    assert overlay.apply("Work/Old/a.txt", idx.item_status("Work/Old/a.txt"), False) == "offloaded"
    assert overlay.apply("Work/b.txt", "synced", False) == "synced"


def test_an_index_built_with_other_offloads_is_rebuilt(monkeypatch, tmp_path):
    _file(tmp_path, "Old/a.txt")
    cat = _cat(_e("b.txt"))
    idx = _indexes(monkeypatch, tmp_path, cat)
    pair, account = SimpleNamespace(id=1), SimpleNamespace()
    old = (("Old", True),)

    async def run():
        plain, _ = await idx.get(pair, account)
        assert plain.overall() == "download" and plain.item_status("Old/a.txt") == "upload"
        assert idx._fresh(1, cat.refreshed_at, old) is None  # offloaded since: not fresh
        idx._cache.clear()
        built, _ = await idx.get(pair, account, old)
        assert built.excluded == old and "Old/a.txt" not in built.status
        assert idx._fresh(1, cat.refreshed_at, old) is built

    asyncio.run(run())
