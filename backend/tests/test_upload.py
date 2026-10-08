import errno
import os
from pathlib import Path

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


def test_upload_during_a_running_sync_succeeds_and_marks_it_dirty(auth_client, env, stub_queue):
    acc, root, pair = env
    stub_queue.running.add(pair.id)
    stub_queue.answer = "dirty"
    r = _upload(auth_client, acc, "New/a.txt", b"x")
    assert r.status_code == 200, r.text
    assert r.json()["syncs"] == {str(pair.id): "dirty"}
    assert (root / "New" / "a.txt").read_bytes() == b"x"


def test_upload_through_a_symlink_to_a_hidden_folder_is_refused(auth_client, env):
    acc, root, _ = env
    (root / "RCLONE_TEST").mkdir()
    (root / "sneaky").symlink_to(root / "RCLONE_TEST")
    assert _upload(auth_client, acc, "a.txt", b"x", path="sneaky").status_code == 404
    assert list((root / "RCLONE_TEST").iterdir()) == []


def test_cross_disk_publish_copies_beside_then_links(tmp_path, monkeypatch):
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "a.txt").write_text("keep")
    tmp = tmp_path / "t.part"
    tmp.write_text("new")
    real = os.link
    calls = []

    def link(src, dst):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real(src, dst)

    monkeypatch.setattr(up.os, "link", link)
    assert up.publish(tmp, folder, "a.txt") == "a (1).txt"
    assert (folder / "a.txt").read_text() == "keep"
    assert (folder / "a (1).txt").read_text() == "new"
    assert not tmp.exists()
    assert sorted(p.name for p in folder.iterdir()) == ["a (1).txt", "a.txt"]


def test_cross_disk_copy_failure_leaves_no_hidden_file(tmp_path, monkeypatch):
    folder = tmp_path / "f"
    folder.mkdir()
    tmp = tmp_path / "t.part"
    tmp.write_text("new")

    def link(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    def boom(src, dst):
        Path(dst).write_text("partial")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(up.os, "link", link)
    monkeypatch.setattr(up.shutil, "copyfile", boom)
    with pytest.raises(OSError):
        up.publish(tmp, folder, "a.txt")
    assert list(folder.iterdir()) == []


def test_not_enough_free_disk_is_refused_before_streaming(auth_client, app, env, monkeypatch):
    acc, root, _ = env
    monkeypatch.setattr(
        "app.api.fileops.shutil.disk_usage",
        lambda p: type("U", (), {"free": 10})(),
    )
    r = _upload(auth_client, acc, "a.bin", b"data")
    assert r.status_code == 507
    assert not (root / "a.bin").exists()
    assert list(app.state.layout.uploads_dir.iterdir()) == []


def test_client_disconnect_cleans_the_temp_file(auth_client, app, env):
    from starlette.requests import ClientDisconnect

    acc, root, _ = env

    def body():
        yield b"some bytes"
        raise ClientDisconnect()

    try:
        r = _upload(auth_client, acc, "gone.bin", body())
        assert r.status_code == 400
    except ClientDisconnect:
        pass  # the test client re-raises it once the server has cleaned up
    assert not (root / "gone.bin").exists()
    assert list(app.state.layout.uploads_dir.iterdir()) == []


def _no_links(monkeypatch):
    def link(src, dst):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(up.os, "link", link)


def test_no_hard_links_never_overwrites_a_file_that_appears_late(tmp_path, monkeypatch):
    folder = tmp_path / "f"
    folder.mkdir()
    tmp = tmp_path / "t.part"
    tmp.write_text("new")
    _no_links(monkeypatch)
    real = up.free_name
    calls = []

    def racing(f, n):
        name = real(f, n)
        if not calls:  # someone else creates that name right after we picked it
            calls.append(1)
            (f / name).write_text("theirs")
        return name

    monkeypatch.setattr(up, "free_name", racing)
    assert up.publish(tmp, folder, "a.txt") == "a (1).txt"
    assert (folder / "a.txt").read_text() == "theirs"
    assert (folder / "a (1).txt").read_text() == "new"


def test_no_hard_links_and_another_disk_still_lands(tmp_path, monkeypatch):
    folder = tmp_path / "f"
    folder.mkdir()
    tmp = tmp_path / "t.part"
    tmp.write_text("new")
    _no_links(monkeypatch)
    real_replace = os.replace

    def replace(src, dst):
        if Path(src) == tmp:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst)

    monkeypatch.setattr(up.os, "replace", replace)
    assert up.publish(tmp, folder, "a.txt") == "a.txt"
    assert (folder / "a.txt").read_text() == "new"
    assert [p.name for p in folder.iterdir()] == ["a.txt"]
    assert not tmp.exists()


def test_failed_replace_removes_the_placeholder(tmp_path, monkeypatch):
    folder = tmp_path / "f"
    folder.mkdir()
    tmp = tmp_path / "t.part"
    tmp.write_text("new")
    _no_links(monkeypatch)

    def replace(src, dst):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(up.os, "replace", replace)
    with pytest.raises(OSError):
        up.publish(tmp, folder, "a.txt")
    assert list(folder.iterdir()) == []


def test_late_cancel_still_queues_the_sync(auth_client, env, stub_queue, monkeypatch):

    from app.api import fileops

    acc, root, pair = env
    real = fileops.moves.finish_in_thread

    async def cancelled_late(fn, *args):
        result, error, _ = await real(fn, *args)
        return result, error, True

    monkeypatch.setattr(fileops.moves, "finish_in_thread", cancelled_late)
    with pytest.raises(BaseException):
        _upload(auth_client, acc, "late.txt", b"x")
    assert (root / "late.txt").read_bytes() == b"x"
    assert stub_queue.requests == [(pair.id, "APP", "sync")]
