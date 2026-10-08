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
