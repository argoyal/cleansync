from app.services.sync.filters import escape_filter_path, render_filters, write_filters


def test_escape_filter_path_specials():
    assert escape_filter_path("a*b?c[d]{e}\\f") == "a\\*b\\?c\\[d\\]\\{e\\}\\\\f"
    assert escape_filter_path("-rf ünï ☃.txt") == "-rf ünï ☃.txt"


def test_render_always_excludes_exports_and_anchors_paths():
    text = render_filters([("Old Stuff", True), ("notes [v2].txt", False)])
    lines = text.splitlines()
    assert lines[0].startswith("#")
    assert "- /.google-exports/**" in lines
    assert "- /Old Stuff/**" in lines
    assert "- /notes \\[v2\\].txt" in lines


def test_write_reports_change_only_when_content_differs(tmp_path):
    f = tmp_path / "f.txt"
    assert write_filters(f, "a\n") is True
    assert write_filters(f, "a\n") is False
    assert write_filters(f, "b\n") is True
    assert f.read_text() == "b\n"


def test_control_characters_rejected():
    import pytest

    for bad in ("a\nb", "a\rb", "a\x00b", "a\x7fb", "a\tb"):
        with pytest.raises(ValueError):
            render_filters([(bad, False)])


def test_trailing_whitespace_uses_character_class():
    assert escape_filter_path("name ") == "name[ ]"
    assert escape_filter_path("a b  ") == "a b[ ][ ]"
    assert escape_filter_path(" lead") == " lead"


def test_upload_temp_files_are_always_excluded_at_any_depth():
    # No leading "/": rclone matches the name in every folder, the pair root included
    # (checked with real rclone; "**/" alone misses the root).
    assert "- .drivesync-upload-*" in render_filters([]).splitlines()


def test_upload_temp_names_are_hidden_from_the_ui():
    from app.services.files.scope import is_hidden, is_hidden_name

    assert is_hidden_name(".drivesync-upload-0123abcd")
    # Same case rule as rclone's filter (case-sensitive), so what the UI hides is exactly
    # what never syncs; a Drive file named ".DriveSync-Upload-x" stays visible and syncs.
    assert not is_hidden_name(".DriveSync-Upload-0123abcd")
    assert is_hidden("Docs/sub/.drivesync-upload-x")
    assert not is_hidden_name("drivesync-upload-x")
    assert not is_hidden_name("notes.drivesync-upload-x")


def test_stale_upload_temp_files_are_swept_at_startup(tmp_path):
    import os
    import time

    from app.services.files.upload import sweep_upload_leftovers

    later = time.time() + 2 * 3600  # "now" for the sweep: every file made here is old by then
    hx = "0123456789abcdef" * 2  # the app names its copies ".drivesync-upload-<uuid4 hex>"
    a = tmp_path / "alice@example.com" / "Docs" / f".drivesync-upload-{hx}"
    b = tmp_path / "alice@example.com" / f".drivesync-upload-{hx}"
    c = tmp_path / "alice@example.com" / "Sub" / f".drivesync-upload-{hx}"
    keep = tmp_path / "alice@example.com" / "Docs" / "report.drivesync-upload-x"
    # Not names the app makes: a user's own file, or one with other casing, is never swept.
    users = tmp_path / "alice@example.com" / "Docs" / ".drivesync-upload-notes"
    upper = tmp_path / "alice@example.com" / "Other" / f".DriveSync-Upload-{hx}"
    for p in (a, b, c, keep, users, upper):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    os.utime(c, (later, later))  # changed just before the sweep
    assert sweep_upload_leftovers(tmp_path, max_age_s=3600, now=later) == 2
    assert not a.exists() and not b.exists()
    assert c.exists() and keep.exists()  # still in use, or not ours
    assert users.exists() and upper.exists()
