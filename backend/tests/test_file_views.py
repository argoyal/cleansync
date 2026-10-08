import os
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Conflict
from app.services.catalog.model import CatalogEntry

NOW = datetime(2026, 10, 8, 8, 0, tzinfo=UTC)


def _write(root, rel, when, data=b"abc"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.utime(p, (when.timestamp(), when.timestamp()))


@pytest.fixture
def world(auth_client, app, make_account, make_pair, save_catalog, stub_queue):
    acc = make_account()
    root = app.state.layout.account_dir(acc.email)
    pair = make_pair(acc, local_subpath="Drive", remote_path="", initialized=True)
    _write(root, "Drive/old.txt", NOW - timedelta(days=3))
    _write(root, "Drive/Work/new report.txt", NOW)
    _write(root, "loose.txt", NOW + timedelta(days=1))  # outside every pair
    save_catalog(
        pair.id,
        [
            CatalogEntry("old.txt", False, 3, NOW - timedelta(days=3), id="O1", starred=True),
            CatalogEntry("Work", True, 0, NOW, id="W1", starred=True),
            CatalogEntry("Work/new report.txt", False, 3, NOW, id="N1"),
            CatalogEntry(
                "Budget Report.xlsx",
                False,
                0,
                NOW - timedelta(hours=1),
                id="B1",
                native="spreadsheet",
                starred=True,
            ),
            CatalogEntry("drive-only report.pdf", False, 9, NOW - timedelta(hours=2), id="D1"),
        ],
    )
    return acc, pair


def _get(client, url, **params):
    r = client.get(url, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_recent_mixes_local_and_drive_newest_first(auth_client, world):
    acc, _ = world
    items = _get(auth_client, "/api/recent", account_id=acc.id)
    assert [i["name"] for i in items] == [
        "new report.txt",
        "Budget Report",
        "drive-only report.pdf",
        "old.txt",
    ]
    assert items[0]["path"] == "Drive/Work/new report.txt"
    assert items[1]["status"] == "native" and items[2]["status"] == "download"
    assert len(_get(auth_client, "/api/recent", account_id=acc.id, limit=2)) == 2


def test_starred_lists_files_folders_and_native_docs(auth_client, world):
    acc, _ = world
    items = _get(auth_client, "/api/starred", account_id=acc.id)
    assert [i["name"] for i in items] == ["Work", "Budget Report", "old.txt"]
    assert items[0]["path"] == "Drive/Work" and items[2]["local"]


def test_search_matches_names_on_both_sides(auth_client, world):
    acc, _ = world
    items = _get(auth_client, "/api/search", account_id=acc.id, q="REPORT")
    assert sorted(i["name"] for i in items) == [
        "Budget Report",
        "drive-only report.pdf",
        "new report.txt",
    ]
    r = auth_client.get("/api/search", params={"account_id": acc.id, "q": " a "})
    assert r.status_code == 422


def test_search_shows_conflicts(auth_client, world, db):
    acc, pair = world
    db.add(
        Conflict(
            pair_id=pair.id,
            path="old.txt",
            local_variant="old.conflict1.txt",
            remote_variant="old.conflict2.txt",
        )
    )
    db.commit()
    items = _get(auth_client, "/api/search", account_id=acc.id, q="old")
    assert items[0]["status"] == "conflict"


def test_storage_reports_drive_quota_and_disk(auth_client, world, db):
    acc, _ = world
    acc.quota_total, acc.quota_used, acc.quota_trash = 100, 40, 5
    db.commit()
    s = _get(auth_client, "/api/storage", account_id=acc.id)
    assert s["drive"] == {"total": 100, "used": 40, "trash": 5, "checked_at": None}
    assert s["disk"]["total"] > 0 and s["disk"]["free"] >= 0
    assert s["synced_bytes"] == 6  # the two files inside the pair, not loose.txt


def test_status_counts_open_conflicts(auth_client, world, db):
    _, pair = world
    db.add(Conflict(pair_id=pair.id, path="a", local_variant="a1", remote_variant="a2"))
    db.add(
        Conflict(
            pair_id=pair.id, path="b", local_variant="b1", remote_variant="b2", state="RESOLVED"
        )
    )
    db.commit()
    assert auth_client.get("/api/status").json()["conflicts"] == 1


def test_views_need_an_existing_account(auth_client):
    for url in ("/api/recent", "/api/starred", "/api/storage"):
        assert auth_client.get(url, params={"account_id": 99}).status_code == 404
