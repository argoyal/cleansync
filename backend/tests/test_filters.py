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
