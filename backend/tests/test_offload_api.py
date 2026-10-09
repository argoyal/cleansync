import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Offload, SyncJob
from app.models.enums import OffloadState as S
from app.services.offload import store
from app.services.queue import MISSING_DISK_MSG
from tests.fake_rclone import check_out
from tests.test_file_ops import NOT_FOUND
from tests.test_offload_run import FakeDrive, lsjson

OLD_A = ("Old/a.txt", 3, "F1")


@pytest.fixture
def env(auth_client, app, make_account, make_pair, stub_queue, monkeypatch):
    acc = make_account()
    pair = make_pair(
        acc, local_subpath="Drive", remote_path="Work", initialized=True, status="HEALTHY"
    )
    root = app.state.layout.account_dir(acc.email)
    (root / "Drive" / "Old").mkdir(parents=True)
    (root / "Drive" / "Old" / "a.txt").write_text("aaa")
    drive = FakeDrive()
    drive.know([OLD_A])  # Drive's trash holds a.txt with the size and MD5 that were checked
    app.state.offloads.drive = drive
    # No real Drive listing in the background: it would run the fake rclone mid-test.
    monkeypatch.setattr(app.state.catalog, "request_refresh", lambda pair_id: None)
    return acc, pair, root


def _wait(client, batch_id):
    for _ in range(100):
        body = client.get(f"/api/offloads/batches/{batch_id}").json()
        if body["done"]:
            return body
        time.sleep(0.05)
    raise AssertionError("the offload did not finish")


def test_preview_lists_items_totals_and_reasons(auth_client, env):
    acc, pair, root = env
    (root / "Loose.txt").write_text("x")
    r = auth_client.post(
        "/api/offloads/preview", json={"account_id": acc.id, "paths": ["Drive/Old", "Loose.txt"]}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_files"] == 1 and body["total_bytes"] == 3 and body["stopped"] is None
    first, second = body["items"]
    assert (first["path"], first["pair_name"], first["blocked"]) == ("Drive/Old", "Docs", None)
    assert (first["pair_id"], first["is_dir"], first["stays_on_drive"]) == (pair.id, True, [])
    assert second["blocked"] == "This isn't in a synced folder, so it isn't on Google Drive."
    assert second["pair_id"] is None and second["pair_name"] is None


def test_offload_runs_in_the_background_and_lists_with_account_paths(
    auth_client, env, fake_rclone, stub_queue, app
):
    acc, pair, _ = env
    events = []
    app.state.bus.publish = lambda kind, data: events.append((kind, data))
    fake_rclone.on("lsjson", stdout=lsjson(OLD_A))
    fake_rclone.on("check", **check_out(["= Old/a.txt"]))
    r = auth_client.post("/api/offloads", json={"account_id": acc.id, "paths": ["Drive/Old"]})
    assert r.status_code == 202, r.text
    assert r.json()["account_id"] == acc.id
    body = _wait(auth_client, r.json()["id"])
    assert body["items"][0]["state"] == "done", body
    assert body["items"][0]["offload_id"] is not None
    [row] = auth_client.get("/api/offloads", params={"account_id": acc.id}).json()
    assert (row["path"], row["state"], row["file_count"], row["busy"]) == (
        "Drive/Old",
        "ACTIVE",
        1,
        False,
    )
    assert (row["pair_name"], row["account_id"], row["trash_verified"]) == ("Docs", acc.id, True)
    assert stub_queue.requests == [(pair.id, "OFFLOAD", "sync")]
    assert ("offload", {"account_id": acc.id, "pair_id": None}) in events
    # The browser shows it as offloaded, and its parent is not "waiting to upload".
    items = auth_client.get("/api/browse", params={"account_id": acc.id, "path": "Drive"}).json()
    assert {i["name"]: i["status"] for i in items["items"]}["Old"] == "offloaded"
    inside = auth_client.get(
        "/api/browse", params={"account_id": acc.id, "path": "Drive/Old"}
    ).json()
    assert {i["name"]: i["status"] for i in inside["items"]}["a.txt"] == "offloaded"


def test_nothing_starts_while_syncing_is_stopped(auth_client, env, stub_queue, db):
    acc, pair, _ = env
    stub_queue.disabled_reason = MISSING_DISK_MSG
    r = auth_client.post("/api/offloads", json={"account_id": acc.id, "paths": ["Drive/Old"]})
    assert r.status_code == 503 and r.json()["detail"] == MISSING_DISK_MSG
    r = auth_client.post(
        "/api/offloads/preview", json={"account_id": acc.id, "paths": ["Drive/Old"]}
    )
    assert r.json()["stopped"] == MISSING_DISK_MSG
    off = Offload(pair_id=pair.id, path="Old", is_dir=True, state=S.ACTIVE, trash_verified=True)
    db.add(off)
    db.commit()
    for action in ("restore", "empty-trash"):
        r = auth_client.post(f"/api/offloads/{off.id}/{action}")
        assert r.status_code == 503 and r.json()["detail"] == MISSING_DISK_MSG, action
    db.refresh(off)
    assert off.state == S.ACTIVE and stub_queue.requests == []


def test_a_busy_folder_is_neither_offloaded_nor_restored(auth_client, env, stub_queue, db):
    acc, pair, _ = env
    stub_queue.running.add(pair.id)
    r = auth_client.post(
        "/api/offloads/preview", json={"account_id": acc.id, "paths": ["Drive/Old"]}
    )
    assert r.json()["items"][0]["blocked"].startswith("This folder is syncing right now")
    off = Offload(pair_id=pair.id, path="Old", is_dir=True, state=S.ACTIVE)
    db.add(off)
    db.commit()
    r = auth_client.post(f"/api/offloads/{off.id}/restore")
    assert r.status_code == 409 and r.json()["detail"].startswith("This folder is syncing")
    db.refresh(off)
    assert off.state == S.ACTIVE


def test_restore_and_empty_trash_routes(auth_client, env, db, app):
    acc, pair, _ = env
    off = Offload(
        pair_id=pair.id,
        path="Old",
        is_dir=True,
        state=S.ACTIVE,
        trashed_ids=["F1"],
        trash_verified=True,
    )
    db.add(off)
    db.commit()
    r = auth_client.post(f"/api/offloads/{off.id}/restore")
    assert r.status_code == 200 and r.json()["state"] == "RESTORING"
    assert r.json()["path"] == "Drive/Old"
    assert auth_client.post(f"/api/offloads/{off.id}/restore").status_code == 409
    assert auth_client.post("/api/offloads/999/restore").status_code == 404
    assert auth_client.post(f"/api/offloads/{off.id}/empty-trash").status_code == 409
    assert auth_client.get("/api/offloads/batches/nope").status_code == 404
    listed = auth_client.get("/api/offloads", params={"account_id": acc.id}).json()
    assert [o["state"] for o in listed] == ["RESTORING"]


def test_empty_trash_starts_in_the_background(auth_client, env, db, app):
    acc, pair, _ = env
    off = Offload(
        pair_id=pair.id,
        path="Old",
        is_dir=True,
        state=S.ACTIVE,
        trashed_ids=["F1"],
        trash_verified=True,
        file_count=1,
        total_bytes=3,
    )
    db.add(off)
    db.commit()
    store.write_manifest(
        app.state.layout,
        off.id,
        [{"path": "Old/a.txt", "size": 3, "md5": "md5-F1", "id": "F1"}],
    )
    service = app.state.offloads
    started = []

    async def held_empty(offload_id):  # stands in for the real work, which keeps it "busy"
        started.append(offload_id)

    service._empty = held_empty
    r = auth_client.post(f"/api/offloads/{off.id}/empty-trash")
    assert r.status_code == 202, r.text
    assert r.json()["busy"] is True and r.json()["state"] == "ACTIVE"
    assert started == [off.id]
    [row] = auth_client.get("/api/offloads", params={"account_id": acc.id}).json()
    assert row["busy"] is True
    service.emptying.clear()


def test_restored_offloads_are_listed_only_when_asked(auth_client, env, db):
    acc, pair, _ = env
    db.add(Offload(pair_id=pair.id, path="Gone", is_dir=False, state=S.RESTORED))
    db.add(Offload(pair_id=pair.id, path="Old", is_dir=True, state=S.ACTIVE))
    db.commit()
    plain = auth_client.get("/api/offloads", params={"account_id": acc.id}).json()
    assert [o["path"] for o in plain] == ["Drive/Old"]
    every = auth_client.get(
        "/api/offloads", params={"account_id": acc.id, "include_restored": True}
    ).json()
    assert [o["path"] for o in every] == ["Drive/Old", "Drive/Gone"]  # newest first
    other = auth_client.get("/api/offloads", params={"account_id": acc.id + 1}).json()
    assert other == []


@pytest.mark.parametrize("action", ["rename", "move", "move-into"])
def test_offloaded_items_cannot_be_renamed_or_moved(auth_client, env, db, action):
    acc, pair, root = env
    (root / "Drive" / "Other").mkdir()
    db.add(Offload(pair_id=pair.id, path="Old", is_dir=True, state=S.ACTIVE))
    db.commit()
    if action == "rename":
        r = auth_client.post(
            "/api/files/rename",
            json={"account_id": acc.id, "path": "Drive/Old/a.txt", "new_name": "b"},
        )
    elif action == "move":
        r = auth_client.post(
            "/api/files/move",
            json={"account_id": acc.id, "paths": ["Drive/Old"], "to": "Drive/Other"},
        )
    else:
        r = auth_client.post(
            "/api/files/move",
            json={"account_id": acc.id, "paths": ["Drive/Other"], "to": "Drive/Old"},
        )
    assert r.status_code == 409
    assert r.json()["detail"].startswith("This is offloaded")
    assert (root / "Drive" / "Old" / "a.txt").exists()
    assert (root / "Drive" / "Other").is_dir()


def test_a_folder_holding_an_offloaded_item_cannot_be_moved_or_renamed(auth_client, env, db):
    acc, pair, root = env
    db.add(Offload(pair_id=pair.id, path="Old/a.txt", is_dir=False, state=S.ACTIVE))
    db.commit()
    r = auth_client.post(
        "/api/files/rename", json={"account_id": acc.id, "path": "Drive/Old", "new_name": "New"}
    )
    assert r.status_code == 409 and r.json()["detail"].startswith("This is offloaded")
    assert (root / "Drive" / "Old" / "a.txt").exists()


def test_a_restored_item_can_be_renamed_again(auth_client, env, db, stub_queue, fake_rclone):
    acc, pair, root = env
    fake_rclone.on("lsjson", **NOT_FOUND)  # Drive doesn't have it: a local rename only
    db.add(Offload(pair_id=pair.id, path="Old", is_dir=True, state=S.RESTORED))
    db.commit()
    r = auth_client.post(
        "/api/files/rename",
        json={"account_id": acc.id, "path": "Drive/Old/a.txt", "new_name": "b.txt"},
    )
    assert r.status_code == 200, r.text
    assert (root / "Drive" / "Old" / "b.txt").exists()


def test_deleting_an_offloaded_file_still_goes_to_recovery(auth_client, env, db, app):
    acc, pair, root = env
    db.add(Offload(pair_id=pair.id, path="Old", is_dir=True, state=S.ACTIVE))
    db.commit()
    r = auth_client.post(
        "/api/files/delete", json={"account_id": acc.id, "paths": ["Drive/Old/a.txt"]}
    )
    assert r.status_code == 200, r.text
    assert not (root / "Drive" / "Old" / "a.txt").exists()
    kept = list((app.state.layout.backups_dir / acc.email / str(pair.id)).rglob("a.txt"))
    assert len(kept) == 1


def test_a_pairs_direction_cannot_be_changed_through_the_api(auth_client, env, db):
    # Offload and Restore need a two-way folder; the API never changes a pair's direction.
    acc, pair, _ = env
    db.add(Offload(pair_id=pair.id, path="Old", is_dir=True, state=S.ACTIVE))
    db.commit()
    r = auth_client.put(
        f"/api/sync-pairs/{pair.id}", json={"name": "Docs", "direction": "REMOTE_TO_LOCAL"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["direction"] == "BIDIRECTIONAL"
    db.refresh(pair)
    assert pair.direction == "BIDIRECTIONAL"


# ---- startup ---------------------------------------------------------------------------
class _Queue:
    disabled_reason = None

    def request(self, pair_id, trigger, action="sync"):
        return "queued"

    def add_finish_listener(self, listener):
        pass

    async def stop(self):
        pass


def _left_by_a_hard_stop(db, layout, make_account, make_pair):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    off = Offload(pair_id=pair.id, path="Old", is_dir=True, state=S.DELETING)
    done = Offload(pair_id=pair.id, path="Done", is_dir=True, state=S.ACTIVE)
    db.add_all([off, done, SyncJob(pair_id=pair.id, kind="OFFLOAD", trigger="OFFLOAD")])
    db.commit()
    stale = store.delete_list_file(layout, 7)
    stale.write_text("a.txt\n")
    store.write_manifest(layout, done.id, [{"path": "Done/x", "size": 1}])
    return off, done, stale


def _check_cleaned(db, layout, off, done, stale):
    db.expire_all()
    assert db.get(Offload, off.id).state == S.ATTENTION
    assert db.get(Offload, off.id).note == store.INTERRUPTED_NOTE
    assert db.get(Offload, done.id).state == S.ACTIVE
    assert not stale.exists()
    assert store.read_manifest(layout, done.id) == [{"path": "Done/x", "size": 1}]


def test_startup_cleans_up_offloads_a_stop_cut_short(settings, layout, db, make_account, make_pair):
    off, done, stale = _left_by_a_hard_stop(db, layout, make_account, make_pair)
    app = create_app(settings)
    app.state.queue = _Queue()
    # A restore finishes when the pair's safe resync is done.
    assert app.state.offloads.resync_done in app.state.initial.on_resync_done
    with TestClient(app):
        _check_cleaned(db, layout, off, done, stale)


def test_startup_cleans_up_offloads_while_syncing_is_stopped(
    settings, layout, db, make_account, make_pair
):
    create_app(settings, start_background=False)  # writes the disk's ID file
    layout.volume_file.unlink()  # ...which is now gone: syncing stops
    off, done, stale = _left_by_a_hard_stop(db, layout, make_account, make_pair)
    app = create_app(settings, start_background=False)
    assert app.state.queue.disabled_reason == MISSING_DISK_MSG
    with TestClient(app):
        _check_cleaned(db, layout, off, done, stale)
