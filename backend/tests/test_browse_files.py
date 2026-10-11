import os
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Conflict
from app.services.catalog.model import CatalogEntry

NOW = datetime(2026, 10, 8, 8, 0, tzinfo=UTC)


def _write(root, rel, data=b"abc"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.utime(p, (NOW.timestamp(), NOW.timestamp()))
    return p


@pytest.fixture
def setup(auth_client, app, make_account, make_pair, stub_queue):
    acc = make_account()
    root = app.state.layout.account_dir(acc.email)
    return acc, root


def _browse(client, acc, path=""):
    r = client.get("/api/browse", params={"account_id": acc.id, "path": path})
    assert r.status_code == 200, r.text
    return {i["name"]: i for i in r.json()["items"]}, r.json()


def test_browse_merges_disk_and_drive(auth_client, setup, make_pair, save_catalog):
    acc, root = setup
    pair = make_pair(acc, initialized=True)
    _write(root, "Work/a.txt")
    _write(root, "local-only.txt")
    _write(root, "RCLONE_TEST")
    save_catalog(
        pair.id,
        [
            CatalogEntry("Work", True, 0, NOW, id="W1"),
            CatalogEntry("Work/a.txt", False, 3, NOW, id="A1", starred=True),
            CatalogEntry("drive-only.pdf", False, 10, NOW, id="P1", mime="application/pdf"),
            CatalogEntry("Plan.docx", False, 0, NOW, id="G1", native="document"),
            CatalogEntry("RCLONE_TEST", False, 0, NOW),
        ],
    )
    items, body = _browse(auth_client, acc)
    assert list(items) == ["Work", "drive-only.pdf", "local-only.txt", "Plan"]
    assert body["pair"]["id"] == pair.id
    assert items["Work"]["status"] == "synced" and items["Work"]["is_dir"]
    assert items["Work"]["google_url"] == "https://drive.google.com/drive/folders/W1"
    assert items["local-only.txt"]["status"] == "upload" and items["local-only.txt"]["local"]
    pdf = items["drive-only.pdf"]
    assert pdf["status"] == "download" and not pdf["local"] and pdf["remote"] and not pdf["thumb"]
    plan = items["Plan"]
    assert plan["status"] == "native" and plan["native"] == "document"
    assert plan["google_url"] == "https://docs.google.com/document/d/G1/edit"
    sub, _ = _browse(auth_client, acc, "Work")
    assert sub["a.txt"]["starred"] and sub["a.txt"]["path"] == "Work/a.txt"


def test_folder_shows_the_most_serious_status_inside(
    auth_client, setup, make_pair, save_catalog, db
):
    acc, root = setup
    pair = make_pair(acc, initialized=True)
    _write(root, "A/deep/x.txt")
    _write(root, "B/y.txt")
    save_catalog(
        pair.id,
        [
            CatalogEntry("A", True, 0, NOW),
            CatalogEntry("A/deep", True, 0, NOW),
            CatalogEntry("B", True, 0, NOW),
            CatalogEntry("B/y.txt", False, 3, NOW),
        ],
    )
    items, _ = _browse(auth_client, acc)
    assert items["A"]["status"] == "upload"
    assert items["B"]["status"] == "synced"
    db.add(
        Conflict(
            pair_id=pair.id,
            path="B/y.txt",
            local_variant="B/y.conflict1.txt",
            remote_variant="B/y.conflict2.txt",
        )
    )
    db.commit()
    items, _ = _browse(auth_client, acc)
    assert items["B"]["status"] == "conflict"


def test_running_pair_shows_pending_items_as_syncing(
    auth_client, setup, make_pair, save_catalog, stub_queue
):
    acc, root = setup
    pair = make_pair(acc, initialized=True)
    _write(root, "new.txt")
    save_catalog(pair.id, [])
    stub_queue.running.add(pair.id)
    items, _ = _browse(auth_client, acc)
    assert items["new.txt"]["status"] == "syncing"


def test_folders_outside_pairs(auth_client, setup, make_pair, save_catalog):
    acc, root = setup
    pair = make_pair(acc, local_subpath="Synced/Docs", remote_path="Docs", initialized=True)
    _write(root, "Synced/Docs/a.txt")
    _write(root, "Loose/b.txt")
    _write(root, "c.txt")
    save_catalog(pair.id, [])
    items, body = _browse(auth_client, acc)
    assert body["pair"] is None
    assert items["Synced"]["status"] == "upload"  # worst state of the pair inside
    assert items["Loose"]["status"] == "unsynced"
    assert items["c.txt"]["status"] == "unsynced" and items["c.txt"]["pair_id"] is None
    inside, body = _browse(auth_client, acc, "Synced/Docs")
    assert body["pair"]["id"] == pair.id and inside["a.txt"]["status"] == "upload"


def test_pair_without_a_listing_yet_is_unknown(auth_client, setup, make_pair):
    acc, root = setup
    make_pair(acc)
    _write(root, "a.txt")
    items, _ = _browse(auth_client, acc)
    assert items["a.txt"]["status"] == "unknown"


def test_thumbnail_flag_and_mime(auth_client, setup, make_pair, save_catalog):
    acc, root = setup
    pair = make_pair(acc)
    save_catalog(pair.id, [])
    _write(root, "photo.JPG")
    _write(root, "notes.md")
    items, _ = _browse(auth_client, acc)
    assert items["photo.JPG"]["mime"] == "image/jpeg" and items["photo.JPG"]["thumb"]
    assert items["notes.md"]["mime"] == "text/markdown" and not items["notes.md"]["thumb"]


@pytest.mark.parametrize("path", ["../x", "a/../../b", "a\x00b"])
def test_unsafe_paths_are_refused(auth_client, setup, path):
    acc, _ = setup
    r = auth_client.get("/api/browse", params={"account_id": acc.id, "path": path})
    assert r.status_code == 422


def test_missing_hidden_and_unknown(auth_client, setup, tmp_path):
    acc, root = setup
    _write(root, ".google-exports/a.docx")
    assert (
        auth_client.get("/api/browse", params={"account_id": acc.id, "path": "nope"}).status_code
        == 404
    )
    assert (
        auth_client.get(
            "/api/browse", params={"account_id": acc.id, "path": ".google-exports"}
        ).status_code
        == 404
    )
    assert auth_client.get("/api/browse", params={"account_id": 999}).status_code == 404


def test_link_leaving_the_account_folder_is_refused(auth_client, setup, tmp_path):
    acc, root = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    r = auth_client.get("/api/browse", params={"account_id": acc.id, "path": "escape"})
    assert r.status_code == 422
    items, _ = _browse(auth_client, acc)
    assert "escape" not in items


def test_browse_needs_login(client):
    assert client.get("/api/browse", params={"account_id": 1}).status_code == 401


def test_a_file_newer_than_the_cached_statuses_is_not_called_synced(
    auth_client, setup, make_pair, save_catalog
):
    acc, root = setup
    pair = make_pair(acc, initialized=True)
    save_catalog(pair.id, [])
    _browse(auth_client, acc)  # builds and caches the statuses
    _write(root, "dropped-in-from-windows.txt")  # no CleanSync action, so no invalidation
    items, _ = _browse(auth_client, acc)
    assert items["dropped-in-from-windows.txt"]["status"] == "upload"


@pytest.mark.parametrize("name", [".GOOGLE-exports", "rclone_test"])
def test_hidden_names_ignore_case(auth_client, setup, make_pair, save_catalog, name):
    acc, root = setup
    pair = make_pair(acc, initialized=True)
    save_catalog(pair.id, [])
    _write(root, f"{name}/a.txt")
    _write(root, "ok.txt")
    items, _ = _browse(auth_client, acc)
    assert list(items) == ["ok.txt"]
    r = auth_client.get("/api/browse", params={"account_id": acc.id, "path": name})
    assert r.status_code == 404


def test_folder_and_file_with_the_same_name_are_not_confused(
    auth_client, setup, make_pair, save_catalog
):
    acc, root = setup
    pair = make_pair(acc, initialized=True)
    _write(root, "x")  # a file here, a folder on Drive
    save_catalog(pair.id, [CatalogEntry("x", True, 0, NOW, id="F1")])
    r = auth_client.get("/api/browse", params={"account_id": acc.id}).json()["items"]
    local = next(i for i in r if not i["is_dir"])
    assert local["status"] == "upload" and not local["remote"]
    assert any(i["is_dir"] and i["remote"] and not i["local"] for i in r)


def test_fresh_file_with_a_different_size_is_not_synced(
    auth_client, setup, make_pair, save_catalog
):
    acc, root = setup
    pair = make_pair(acc, initialized=True)
    save_catalog(pair.id, [CatalogEntry("n.txt", False, 99, NOW - timedelta(hours=1), id="N1")])
    _browse(auth_client, acc)  # caches the index
    _write(root, "n.txt")  # 3 bytes, same mtime, appears after the index
    items, _ = _browse(auth_client, acc)
    assert items["n.txt"]["status"] == "upload"


def test_items_added_after_the_cached_index_are_judged_on_the_spot(
    auth_client, setup, make_pair, save_catalog
):
    acc, root = setup
    pair = make_pair(acc, initialized=True)
    _write(root, "Work/a.txt")
    save_catalog(
        pair.id,
        [CatalogEntry("Work", True, 0, NOW, id="W1"), CatalogEntry("Work/a.txt", False, 3, NOW)],
    )
    items, _ = _browse(auth_client, acc)  # builds and caches the index
    assert items["Work"]["status"] == "synced"
    # A folder upload adds a new folder without dropping the cached index.
    _write(root, "Trip/day 1.jpg")
    (root / "Empty").mkdir()
    items, _ = _browse(auth_client, acc)
    assert items["Trip"]["status"] == "upload" and items["Empty"]["status"] == "upload"
    assert items["Work"]["status"] == "synced"


def test_a_pairs_own_folder_is_marked(auth_client, setup, make_pair, save_catalog):
    acc, root = setup
    pair = make_pair(acc, local_subpath="Projects", remote_path="Projects", initialized=True)
    _write(root, "Projects/Inner/a.txt")
    (root / "Loose").mkdir()
    save_catalog(pair.id, [])
    top, _ = _browse(auth_client, acc)
    assert top["Projects"]["pair_root"] is True
    # Its own folder seen from the parent shows the pair's state, never "Not synced".
    assert top["Projects"]["status"] == "upload"
    assert (top["Loose"]["pair_root"], top["Loose"]["status"]) == (False, "unsynced")
    inner, _ = _browse(auth_client, acc, "Projects")
    assert [i["pair_root"] for i in inner.values()] == [False]


def test_a_whole_account_pair_marks_no_folder(auth_client, setup, make_pair, save_catalog):
    acc, root = setup
    pair = make_pair(acc, initialized=True)
    _write(root, "Work/a.txt")
    save_catalog(pair.id, [])
    top, _ = _browse(auth_client, acc)
    assert top["Work"]["pair_root"] is False
