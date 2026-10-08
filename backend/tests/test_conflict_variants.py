from app.services.sync.conflicts import find_variants


def test_variants_keep_extension(tmp_path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "report.conflict1.xlsx").write_text("l")
    (tmp_path / "dir" / "report.conflict2.xlsx").write_text("r")
    assert find_variants(tmp_path, "dir/report.xlsx") == (
        "dir/report.conflict1.xlsx",
        "dir/report.conflict2.xlsx",
    )


def test_variants_appended_suffix(tmp_path):
    (tmp_path / "notes.conflict1").write_text("l")
    (tmp_path / "notes.conflict2").write_text("r")
    assert find_variants(tmp_path, "notes") == ("notes.conflict1", "notes.conflict2")


def test_missing_variants(tmp_path):
    assert find_variants(tmp_path, "nothing.txt") is None


def test_variants_pick_the_newest_complete_pair(tmp_path):
    for n in (1, 2, 3, 4, 5):
        (tmp_path / f"r.conflict{n}.txt").write_text("x")
    assert find_variants(tmp_path, "r.txt") == ("r.conflict3.txt", "r.conflict4.txt")
