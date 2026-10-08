import errno
import os

import pytest

from app.services.files import upload as up
from app.services.files.names import free_name


@pytest.fixture
def env(auth_client, app, make_account, make_pair, stub_queue):
    acc = make_account()
    root = app.state.layout.account_dir(acc.email)
    pair = make_pair(acc, initialized=True)
    return acc, root, pair


def _upload(client, acc, name, data, path="", **params):
    return client.post(
        "/api/files/upload",
        params={"account_id": acc.id, "path": path, "name": name, **params},
        content=data,
    )


def test_upload_streams_into_place_and_queues_a_sync(auth_client, app, env, stub_queue):
    acc, root, pair = env
    (root / "Docs").mkdir()
    r = _upload(
        auth_client, acc, "report.pdf", b"%PDF-1.4 hello", path="Docs", mtime_ms=1_700_000_000_000
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"path": "Docs/report.pdf", "size": 14, "syncs": {str(pair.id): "queued"}}
    f = root / "Docs" / "report.pdf"
    assert f.read_bytes() == b"%PDF-1.4 hello"
    assert int(f.stat().st_mtime) == 1_700_000_000
    assert list(app.state.layout.uploads_dir.iterdir()) == []
    assert stub_queue.requests == [(pair.id, "APP", "sync")]


def test_same_name_keeps_both(auth_client, env):
    acc, root, _ = env
    (root / "a.txt").write_text("old")
    r = _upload(auth_client, acc, "a.txt", b"new")
    assert r.json()["path"] == "a (1).txt"
    assert (root / "a.txt").read_text() == "old"


def test_folder_upload_creates_sub_folders(auth_client, env):
    acc, root, _ = env
    r = _upload(auth_client, acc, "Trip/day 1/photo.jpg", b"jpg")
    assert r.json()["path"] == "Trip/day 1/photo.jpg"
    assert (root / "Trip" / "day 1" / "photo.jpg").read_bytes() == b"jpg"


def test_large_upload_arrives_in_pieces(auth_client, env):
    acc, root, _ = env
    piece = b"z" * (1024 * 1024)

    def body():
        for _ in range(9):
            yield piece

    r = _upload(auth_client, acc, "big.bin", body())
    assert r.json()["size"] == 9 * len(piece)
    assert (root / "big.bin").stat().st_size == 9 * len(piece)


def test_cut_off_upload_is_thrown_away(auth_client, app, env):
    acc, root, _ = env
    r = auth_client.post(
        "/api/files/upload",
        params={"account_id": acc.id, "name": "half.bin"},
        content=b"12345",
        headers={"content-length": "999"},
    )
    assert r.status_code == 400
    assert not (root / "half.bin").exists()
    assert list(app.state.layout.uploads_dir.iterdir()) == []


@pytest.mark.parametrize("name", ["../x.txt", "a/../../b.txt", "RCLONE_TEST", "dir/", "a\x00b"])
def test_unsafe_names_are_refused(auth_client, env, name):
    acc, root, _ = env
    assert _upload(auth_client, acc, name, b"x").status_code == 422


def test_full_disk_is_reported(auth_client, app, env, monkeypatch):
    acc, _, _ = env

    async def full(stream, tmp):
        tmp.write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("app.api.fileops.save_stream", full)
    r = _upload(auth_client, acc, "a.bin", b"data")
    assert r.status_code == 507
    assert list(app.state.layout.uploads_dir.iterdir()) == []


def test_upload_refused_while_syncing_is_disabled(auth_client, env, stub_queue):
    acc, root, _ = env
    stub_queue.disabled_reason = "db"
    assert _upload(auth_client, acc, "a.txt", b"x").status_code == 503
    assert not (root / "a.txt").exists()


def test_publish_never_overwrites(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "a.txt").write_text("keep")
    tmp = tmp_path / "t.part"
    tmp.write_text("new")
    assert up.publish(tmp, folder, "a.txt") == "a (1).txt"
    assert (folder / "a.txt").read_text() == "keep" and not tmp.exists()


def test_publish_without_hard_links_falls_back_to_rename(tmp_path, monkeypatch):
    folder = tmp_path / "f"
    folder.mkdir()
    tmp = tmp_path / "t.part"
    tmp.write_text("new")

    def no_links(src, dst):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(up.os, "link", no_links)
    assert up.publish(tmp, folder, "a.txt") == "a.txt"
    assert (folder / "a.txt").read_text() == "new"


def test_free_name_handles_dotfiles_and_extensions(tmp_path):
    (tmp_path / ".env").write_text("x")
    (tmp_path / "a.tar.gz").write_text("x")
    assert free_name(tmp_path, ".env") == ".env (1)"
    assert free_name(tmp_path, "a.tar.gz") == "a.tar (1).gz"
    assert free_name(tmp_path, "new.txt") == "new.txt"


def test_leftover_temp_files_are_cleared_at_startup(layout):
    (layout.uploads_dir / "old.part").write_bytes(b"x")
    assert up.clear_uploads(layout) == 1
    assert list(layout.uploads_dir.iterdir()) == []
    assert os.path.isdir(layout.uploads_dir)


def test_upload_into_a_busy_pair_is_refused_and_creates_nothing(auth_client, env, stub_queue):
    acc, root, pair = env
    stub_queue.running.add(pair.id)
    r = _upload(auth_client, acc, "New/a.txt", b"x")
    assert r.status_code == 409
    assert not (root / "New").exists()


def test_upload_through_a_symlink_to_a_hidden_folder_is_refused(auth_client, env):
    acc, root, _ = env
    (root / "RCLONE_TEST").mkdir()
    (root / "sneaky").symlink_to(root / "RCLONE_TEST")
    assert _upload(auth_client, acc, "a.txt", b"x", path="sneaky").status_code in (404, 422)
    assert list((root / "RCLONE_TEST").iterdir()) == []
