import json
from datetime import UTC, datetime

from app.services.catalog.model import Catalog, CatalogEntry, google_url, parse_item
from app.services.catalog.store import CatalogStore

T = "2026-10-08T08:00:00.123456789Z"


def _drive(path, *, mime="text/plain", size=10, is_dir=False, id_="abc_123-X", starred="false"):
    return {
        "Path": path,
        "Name": path.rsplit("/", 1)[-1],
        "Size": size,
        "MimeType": mime,
        "ModTime": T,
        "IsDir": is_dir,
        "ID": id_,
        "Metadata": {"starred": starred, "content-type": mime},
    }


def test_parse_drive_file_with_metadata():
    e = parse_item(_drive("Work/a.txt", starred="true"))
    assert e.path == "Work/a.txt" and e.name == "a.txt" and e.parent == "Work"
    assert e.id == "abc_123-X" and e.starred and e.native is None and e.size == 10
    assert e.mtime == datetime(2026, 10, 8, 8, 0, 0, 123456, tzinfo=UTC)


def test_parse_local_item_without_drive_fields():
    e = parse_item({"Path": "a.txt", "Name": "a.txt", "Size": 3, "IsDir": False})
    assert e.id is None and e.mime is None and not e.starred and e.mtime is None


def test_native_doc_by_google_mime():
    e = parse_item(_drive("Plan", mime="application/vnd.google-apps.form", size=-1))
    assert e.native == "form" and e.size == 0


def test_native_doc_listed_under_its_export_name():
    e = parse_item(
        _drive(
            "Notes/Report.docx",
            size=-1,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    )
    assert e.native == "document"
    assert e.display_name == "Report"


def test_folder_with_unknown_size_is_not_a_native_doc():
    e = parse_item(_drive("Work", mime="inode/directory", size=-1, is_dir=True))
    assert e.is_dir and e.native is None


def test_bad_ids_and_paths_are_ignored():
    assert parse_item(_drive("a.txt", id_="../../x")).id is None
    assert parse_item({"Path": "", "IsDir": False}) is None
    assert parse_item({"Path": "/etc/passwd", "IsDir": False}) is None
    assert parse_item({"Name": "x"}) is None


def test_google_urls():
    folder = CatalogEntry("W", True, 0, None, id="F1")
    doc = CatalogEntry("R.docx", False, 0, None, id="D1", native="document")
    sheet = CatalogEntry("S.xlsx", False, 0, None, id="S1", native="spreadsheet")
    form = CatalogEntry("Q", False, 0, None, id="Q1", native="form")
    office = CatalogEntry("Budget.xlsx", False, 5, None, id="O1")
    pdf = CatalogEntry("a.pdf", False, 5, None, id="P1")
    assert google_url(folder) == "https://drive.google.com/drive/folders/F1"
    assert google_url(doc) == "https://docs.google.com/document/d/D1/edit"
    assert google_url(sheet) == "https://docs.google.com/spreadsheets/d/S1/edit"
    assert google_url(form) == "https://drive.google.com/open?id=Q1"
    assert google_url(office) == "https://docs.google.com/spreadsheets/d/O1/edit?rtpof=true&sd=true"
    assert google_url(pdf) == "https://drive.google.com/file/d/P1/view"
    assert google_url(CatalogEntry("x", False, 1, None)) is None


def _catalog() -> Catalog:
    items = [
        _drive("Work", mime="inode/directory", size=-1, is_dir=True, id_="W1"),
        _drive("Work/a.txt", size=4),
        _drive("Work/Report.docx", size=-1, mime="application/x", id_="R1"),
        _drive("b.bin", size=6),
    ]
    return Catalog.build(1, datetime(2026, 10, 8, tzinfo=UTC), "root", map(parse_item, items))


def test_catalog_children_folder_ids_and_summary():
    cat = _catalog()
    assert sorted(e.name for e in cat.children("")) == ["Work", "b.bin"]
    assert sorted(e.name for e in cat.children("Work")) == ["Report.docx", "a.txt"]
    assert "Work/Report.docx" in cat.natives and "Work/Report.docx" not in cat.files
    assert cat.folder_id("") == "root" and cat.folder_id("Work") == "W1"
    assert cat.folder_id("Work/a.txt") is None and cat.folder_id("Nope") is None
    assert cat.summary() == {"files": 2, "folders": 1, "bytes": 10, "native_docs": 1}


def test_store_round_trip_and_cache(tmp_path):
    store = CatalogStore(tmp_path / "catalog")
    assert store.load(1) is None
    store.save(_catalog())
    first = store.load(1)
    assert first is store.load(1)  # cached while the file is unchanged
    assert first.files["Work/a.txt"].size == 4
    assert first.natives["Work/Report.docx"].id == "R1"
    assert list((tmp_path / "catalog").iterdir()) == [tmp_path / "catalog" / "1.json"]


def test_store_upsert_adds_an_entry(tmp_path):
    store = CatalogStore(tmp_path)
    store.save(_catalog())
    store.upsert(1, CatalogEntry("Work/New.docx", False, 0, None, id="N1", native="document"))
    cat = store.load(1)
    assert cat.natives["Work/New.docx"].id == "N1"
    assert cat.files["Work/a.txt"].size == 4


def test_store_ignores_a_corrupt_file(tmp_path):
    store = CatalogStore(tmp_path)
    (tmp_path / "1.json").write_text("{not json")
    assert store.load(1) is None
    (tmp_path / "1.json").write_text(json.dumps({"pair_id": 1}))
    assert store.load(1) is None


def test_deleting_a_pair_removes_its_catalog(auth_client, make_account, app):
    acc = make_account()
    pair = auth_client.post("/api/sync-pairs", json={"account_id": acc.id, "name": "D"}).json()
    f = app.state.layout.catalog_dir / f"{pair['id']}.json"
    f.write_text("{}")
    assert auth_client.delete(f"/api/sync-pairs/{pair['id']}").status_code == 204
    assert not f.exists()


def test_store_serializes_upsert_and_save(tmp_path):
    import threading

    store = CatalogStore(tmp_path)
    store.save(_catalog())
    newer = Catalog.build(
        1, datetime(2026, 10, 9, tzinfo=UTC), "root", [CatalogEntry("Fresh.txt", False, 1, None)]
    )
    doc = CatalogEntry("Work/New.docx", False, 0, None, id="N1", native="document")
    # upsert after save: both survive
    store.save(newer)
    store.upsert(1, doc)
    cat = store.load(1)
    assert "Fresh.txt" in cat.files and "Work/New.docx" in cat.natives
    # save after upsert: the refresh wins (it is the newer full listing), file stays valid
    store.upsert(1, CatalogEntry("Later.docx", False, 0, None, id="L1", native="document"))
    store.save(newer)
    assert store.load(1).files.keys() == {"Fresh.txt"}
    # concurrent: never a torn file, and every outcome is one of the serial orders
    for _ in range(20):
        t1 = threading.Thread(target=store.save, args=(newer,))
        t2 = threading.Thread(target=store.upsert, args=(1, doc))
        t1.start(), t2.start(), t1.join(), t2.join()
        cat = store.load(1)
        assert "Fresh.txt" in cat.files


def test_store_logs_a_corrupt_file_once(tmp_path, caplog):
    store = CatalogStore(tmp_path)
    (tmp_path / "1.json").write_text("[" * 100000)  # RecursionError in json
    with caplog.at_level("WARNING"):
        assert store.load(1) is None
        assert store.load(1) is None
    assert len([r for r in caplog.records if "unreadable" in r.message]) == 1


def test_stored_listing_is_revalidated(tmp_path):
    from app.services.catalog.model import valid_id

    assert valid_id("../x") is None and valid_id("ok_1-A") == "ok_1-A"
    assert valid_id("F1\n") is None  # `$` alone would let a trailing line break through
    assert parse_item({"Path": "a/../b", "IsDir": False}) is None
    base = {"pair_id": 1, "refreshed_at": "2026-10-08T00:00:00+00:00", "root_id": None}
    ok = {"path": "a.txt", "dir": False, "size": 1, "mtime": None, "id": "../bad"}
    cat = Catalog.from_json({**base, "entries": [ok]})
    assert cat.files["a.txt"].id is None
    for bad in ("/etc/passwd", "a/../../b", "x\x00y", ""):
        try:
            Catalog.from_json({**base, "entries": [{**ok, "path": bad}]})
        except ValueError:
            continue
        raise AssertionError(bad)
    store = CatalogStore(tmp_path)
    (tmp_path / "1.json").write_text(json.dumps({**base, "entries": [{**ok, "path": "../x"}]}))
    assert store.load(1) is None


def test_delete_pair_evicts_through_the_store(tmp_path):
    store = CatalogStore(tmp_path)
    store.save(_catalog())
    assert store.load(1) is not None
    store.delete(1)
    assert store.load(1) is None and not (tmp_path / "1.json").exists()
