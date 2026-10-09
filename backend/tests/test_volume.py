import asyncio
import threading

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SettingRow
from app.services.queue import MISSING_DISK_MSG, SyncQueue
from app.services.volume import VOLUME_KEY, VolumeGuard, read_volume_file

ID_A = "a" * 32
ID_B = "b" * 32


def _stored_id(db):
    db.expire_all()
    row = db.get(SettingRow, VOLUME_KEY)
    return None if row is None else row.value


def test_first_start_writes_the_volume_file_and_stores_the_same_id(app, db):
    vf = app.state.layout.volume_file
    assert vf.name == ".drivesync-volume" and vf.parent == app.state.layout.root
    vid = read_volume_file(vf)
    assert vid is not None and len(vid) == 32
    assert _stored_id(db) == vid
    assert app.state.queue.disabled_reason is None


def test_restart_with_the_same_disk_keeps_syncing_on(app, settings, db):
    vid = read_volume_file(app.state.layout.volume_file)
    again = create_app(settings, start_background=False)
    assert again.state.queue.disabled_reason is None
    assert read_volume_file(again.state.layout.volume_file) == vid == _stored_id(db)


def test_missing_volume_file_at_startup_stops_syncing_and_is_not_recreated(app, settings):
    vf = app.state.layout.volume_file
    vf.unlink()
    again = create_app(settings, start_background=False)
    assert again.state.queue.disabled_reason == MISSING_DISK_MSG
    assert again.state.queue.request(1, "MANUAL") == "disabled"
    assert not vf.exists()


def test_a_garbled_or_different_volume_file_stops_syncing_and_is_left_alone(app, settings):
    vf = app.state.layout.volume_file
    for content in ("", "not an id\n", ID_B + "\n"):
        vf.write_text(content)
        again = create_app(settings, start_background=False)
        assert again.state.queue.disabled_reason == MISSING_DISK_MSG, content
        assert vf.read_text() == content


def test_a_database_without_an_id_adopts_the_file_on_the_disk(app, settings, db):
    # e.g. a database copy restored from before this check existed
    vf = app.state.layout.volume_file
    vf.write_text(ID_A + "\n")
    db.delete(db.get(SettingRow, VOLUME_KEY))
    db.commit()
    again = create_app(settings, start_background=False)
    assert again.state.queue.disabled_reason is None
    assert _stored_id(db) == ID_A
    assert vf.read_text() == ID_A + "\n"


async def test_disk_gone_while_running_refuses_the_next_job_and_runs_nothing(tmp_path):
    vf = tmp_path / ".drivesync-volume"
    vf.write_text(ID_A + "\n")
    runs: list[int] = []
    gate = asyncio.Event()

    async def handler(pair_id, action, trigger):
        runs.append(pair_id)
        if pair_id == 1:
            await gate.wait()

    stops: list[str] = []
    q = SyncQueue(handler, max_parallel=1, guard=VolumeGuard(vf, ID_A).check, on_stop=stops.append)
    assert q.request(1, "MANUAL") == "queued"
    await asyncio.sleep(0.01)
    assert q.request(2, "SCHEDULE") == "queued"  # waits for the one free slot
    assert q.request(1, "WATCHER") == "dirty"  # a re-run of pair 1 is waiting too
    vf.unlink()  # the disk drops out while pair 1 runs
    gate.set()
    await q.wait_idle()
    assert runs == [1]  # neither the queued pair 2 nor pair 1's re-run started
    assert q.disabled_reason == MISSING_DISK_MSG
    assert stops == [MISSING_DISK_MSG]  # said once
    assert q.request(3, "MANUAL") == "disabled"
    assert q.request(1, "INITIAL", action="preview") == "disabled"
    assert not vf.exists()  # never recreated
    # The disk coming back does not restart syncing: the message says to restart DriveSync.
    vf.write_text(ID_A + "\n")
    assert q.request(3, "MANUAL") == "disabled"
    await q.wait_idle()
    assert runs == [1]


def test_disk_gone_turns_sync_now_into_a_clear_503_and_shows_on_status(
    auth_client, app, make_account, make_pair
):
    pair = make_pair(make_account(), initialized=True)
    assert auth_client.get("/api/status").json()["sync_stopped"] is None
    app.state.layout.volume_file.unlink()
    r = auth_client.post(f"/api/sync-pairs/{pair.id}/sync")
    assert r.status_code == 503
    assert r.json()["detail"] == MISSING_DISK_MSG
    assert auth_client.get("/api/status").json()["sync_stopped"] == MISSING_DISK_MSG
    assert auth_client.get("/api/health").json()["syncing"] == "stopped"


def test_volume_id_is_not_a_user_setting(auth_client):
    assert VOLUME_KEY not in auth_client.get("/api/settings").json()
    assert auth_client.put("/api/settings", json={VOLUME_KEY: ID_B}).status_code == 422


async def test_the_disk_check_reads_the_file_off_the_event_loop(tmp_path):
    vf = tmp_path / ".drivesync-volume"
    vf.write_text(ID_A + "\n")
    guard = VolumeGuard(vf, ID_A)
    threads: list[bool] = []

    def check():
        threads.append(threading.current_thread() is threading.main_thread())
        return guard.check()

    async def handler(pair_id, action, trigger):
        return None

    q = SyncQueue(handler, guard=check)
    assert await q.check() is False
    assert q.request(1, "MANUAL") == "queued"
    await q.wait_idle()
    assert threads and not any(threads)  # a hung disk can only block a worker thread
    vf.unlink()
    assert await q.check() is True
    assert q.disabled_reason == MISSING_DISK_MSG


def test_disk_missing_at_startup_skips_recovery_schedule_and_watcher(settings, monkeypatch):
    first = create_app(settings, start_background=False)  # writes the volume file
    first.state.layout.volume_file.unlink()
    recovered: list[object] = []
    monkeypatch.setattr("app.main.recover_interrupted", lambda db: recovered.append(db) or [])
    app = create_app(settings)  # background services on, as in production
    with TestClient(app):
        assert app.state.queue.disabled_reason == MISSING_DISK_MSG
        assert recovered == []
        assert not app.state.scheduler.aps.running
        assert app.state.watcher._observer is None
    assert not app.state.scheduler.aps.running  # and it shut down cleanly


def test_first_start_that_cannot_write_the_file_stops_syncing_and_retries_later(
    settings, monkeypatch
):
    def fail(path, volume_id):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr("app.services.volume._write_volume_file", fail)
    app = create_app(settings, start_background=False)
    assert app.state.queue.disabled_reason == MISSING_DISK_MSG
    assert not app.state.layout.volume_file.exists()
    with app.state.session_factory() as db:
        assert db.get(SettingRow, VOLUME_KEY) is None  # nothing stored: the next start retries
    monkeypatch.undo()
    again = create_app(settings, start_background=False)
    assert again.state.queue.disabled_reason is None
    assert read_volume_file(again.state.layout.volume_file) is not None
