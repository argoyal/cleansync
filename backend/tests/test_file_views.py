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


def _names(items):
    return [i["name"] for i in items]


def test_hidden_natives_never_listed(auth_client, world, save_catalog):
    acc, pair = world
    save_catalog(
        pair.id,
        [
            CatalogEntry("ok report.txt", False, 1, NOW, id="K1", starred=True),
            CatalogEntry(
                ".google-exports/x report", False, 0, NOW, id="H1", native="document", starred=True
            ),
            CatalogEntry(
                "RCLONE_TEST/y report", False, 0, NOW, id="H2", native="document", starred=True
            ),
            CatalogEntry(
                "rclone_test/y report2", False, 0, NOW, id="H3", native="document", starred=True
            ),
        ],
    )
    for url, extra in (("/api/recent", {}), ("/api/search", {"q": "report"}), ("/api/starred", {})):
        names = _names(_get(auth_client, url, account_id=acc.id, **extra))
        assert not [n for n in names if n.startswith(("x ", "y "))], (url, names)
    assert "ok report.txt" in _names(
        _get(auth_client, "/api/search", account_id=acc.id, q="report")
    )


def test_search_cap_and_ranking_across_pairs(
    auth_client, app, make_account, make_pair, save_catalog, stub_queue
):
    acc = make_account()
    p1 = make_pair(acc, local_subpath="A", remote_path="", initialized=True)
    p2 = make_pair(acc, local_subpath="B", remote_path="", initialized=True)
    save_catalog(
        p1.id, [CatalogEntry(f"zz{i:03}.txt", False, 1, NOW, id=f"a{i}") for i in range(250)]
    )
    save_catalog(p2.id, [CatalogEntry("aa-first.txt", False, 1, NOW, id="b1")])
    items = _get(auth_client, "/api/search", account_id=acc.id, q="txt")
    assert len(items) == 200 and items[0]["name"] == "aa-first.txt"
    assert len(_get(auth_client, "/api/recent", account_id=acc.id, limit=500)) == 200


def test_search_unicode_and_regex_chars(auth_client, world, save_catalog):
    acc, pair = world
    save_catalog(
        pair.id,
        [
            CatalogEntry("Straße.txt", False, 1, NOW, id="u1"),
            CatalogEntry("école.txt", False, 1, NOW, id="u2"),  # decomposed e-acute
            CatalogEntry("a[b].txt", False, 1, NOW, id="u3"),
            CatalogEntry("plain.txt", False, 1, NOW, id="u4"),
        ],
    )
    s = lambda q: _names(_get(auth_client, "/api/search", account_id=acc.id, q=q))  # noqa: E731
    assert s("STRASSE") == ["Stra\u00dfe.txt"]
    assert s("straße") == ["Straße.txt"]
    assert s("Éc") == ["école.txt"]
    assert s(".*") == []
    assert s("[b") == ["a[b].txt"]


def test_search_query_length_and_limit_clamp(auth_client, world):
    acc, _ = world
    assert (
        auth_client.get("/api/search", params={"account_id": acc.id, "q": "ol"}).status_code == 200
    )
    assert (
        auth_client.get("/api/search", params={"account_id": acc.id, "q": "o"}).status_code == 422
    )
    for lim in (0, -5):
        assert len(_get(auth_client, "/api/recent", account_id=acc.id, limit=lim)) == 1


def test_other_account_files_never_appear(
    auth_client, world, make_account, make_pair, app, save_catalog
):
    acc, _ = world
    other = make_account("bob@example.com")
    op = make_pair(other, local_subpath="Drive", remote_path="", initialized=True)
    _write(app.state.layout.account_dir(other.email), "Drive/secret report.txt", NOW)
    save_catalog(op.id, [CatalogEntry("secret report.txt", False, 3, NOW, id="S1", starred=True)])
    for url, extra in (("/api/recent", {}), ("/api/search", {"q": "report"}), ("/api/starred", {})):
        assert "secret report.txt" not in _names(_get(auth_client, url, account_id=acc.id, **extra))


def test_storage_null_quota_and_missing_root(auth_client, make_account, app):
    acc = make_account("carol@example.com")
    root = app.state.layout.account_dir(acc.email)
    import shutil

    shutil.rmtree(root)
    s = _get(auth_client, "/api/storage", account_id=acc.id)
    assert s["drive"] == {"total": None, "used": None, "trash": None, "checked_at": None}
    assert s["disk"]["total"] > 0 and s["synced_bytes"] is None
    assert not root.exists()
