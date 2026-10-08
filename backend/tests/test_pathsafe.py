import os

import pytest

from app.core.pathsafe import UnsafePath, normalize_rel, safe_join, validate_remote_path


def test_join_inside_base(tmp_path):
    assert safe_join(tmp_path, "a/b.txt") == tmp_path / "a" / "b.txt"
    assert safe_join(tmp_path, "") == tmp_path
    assert safe_join(tmp_path, "/a/") == tmp_path / "a"


@pytest.mark.parametrize("bad", ["../x", "a/../../x", "a/..", "a\x00b", "..", "a/./../../etc"])
def test_join_rejects_escape(tmp_path, bad):
    with pytest.raises(UnsafePath):
        safe_join(tmp_path, bad)


def test_join_rejects_symlink_pointing_outside(tmp_path):
    base = tmp_path / "base"
    outside = tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    os.symlink(outside, base / "link")
    with pytest.raises(UnsafePath):
        safe_join(base, "link/secret.txt")


def test_symlink_inside_base_is_fine(tmp_path):
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", tmp_path / "alias")
    assert safe_join(tmp_path, "alias/x") == tmp_path / "alias" / "x"


def test_odd_but_legal_names_are_kept(tmp_path):
    assert normalize_rel("-rf [1] *.txt/ünï ☃") == "-rf [1] *.txt/ünï ☃"


@pytest.mark.parametrize("bad", ["../Docs", "Docs/../..", "a\x00"])
def test_remote_path_rules(bad):
    with pytest.raises(UnsafePath):
        validate_remote_path(bad)


def test_remote_path_trims_slashes():
    assert validate_remote_path("/Work/Projects/") == "Work/Projects"
