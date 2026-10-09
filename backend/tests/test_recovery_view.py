import zipfile
from io import BytesIO

import pytest

from app.models import Offload
from app.models.enums import OffloadState as S
from app.services.files.recovery import FOLDER_GONE_MSG, restored_names, unconflict
from app.services.queue import MISSING_DISK_MSG


def _put(base, rel, text="x"):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.fixture
def env(auth_client, app, make_account, make_pair, stub_queue, db):
    acc = make_account()
    pair = make_pair(acc, local_subpath="Docs", initialized=True, backup_retention_days=7)
    root = app.state.layout.account_dir(acc.email)
    (root / "Docs").mkdir()
    b = app.state.layout.backups_dir / acc.email
    P = str(pair.id)
    _put(b, f"{P}/2026-10-01/report.txt", "sync backup")
    _put(b, f"{P}/2026-10-02/.drivesync-deleted-101010-ab12/Sub/a.txt", "deleted")
    _put(b, f"{P}/2026-10-02/.drivesync-initial-5/b.txt", "first")
    _put(b, f"{P}/2026-10-03/.drivesync-resync-9/c.txt", "resync")
    _put(b, f"{P}/2026-10-03/conflicts/notes.conflict2.txt", "loser")
    _put(b, "unsynced/2026-10-04/.drivesync-deleted-111111-cd34/Loose/x.txt", "loose")
    _put(b, "99/2026-10-04/z.txt", "orphan")  # its pair was removed
    _put(b, "unsynced/2026-10-04/stray.txt", "not ours")  # not made by DriveSync: ignored
    off = Offload(
        pair_id=pair.id,
        path="Old",
        is_dir=True,
        state=S.ACTIVE,
        total_bytes=9,
        trashed_ids=["F1"],
        trash_verified=True,
    )
    db.add(off)
    db.commit()
    return acc, pair, root, off


def _list(client, acc, **params):
    r = client.get("/api/recovery", params={"account_id": acc.id, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_entries_say_where_they_came_from_and_when_they_go(auth_client, env):
    acc, pair, _, off = env
    body = _list(auth_client, acc)
    by_name = {e["name"]: e for e in body["entries"]}
    assert body["total"] == 8
    assert (by_name["report.txt"]["kind"], by_name["report.txt"]["original_path"]) == (
        "sync_backup",
        "Docs/report.txt",
    )
    assert by_name["report.txt"]["expires_on"] == "2026-10-09"  # day + 7 days kept + 1
    assert by_name["a.txt"]["kind"] == "deleted"
    assert by_name["a.txt"]["original_path"] == "Docs/Sub/a.txt"
    assert by_name["b.txt"]["kind"] == "first_sync" and by_name["c.txt"]["kind"] == "resync"
    assert (by_name["notes.txt"]["kind"], by_name["notes.txt"]["original_path"]) == (
        "conflict",
        "Docs/notes.txt",
    )
    assert by_name["x.txt"]["original_path"] == "Loose/x.txt"
    assert by_name["x.txt"]["expires_on"] == "2026-11-04"  # default retention: 30 days
    assert by_name["z.txt"]["original_path"] is None and not by_name["z.txt"]["restorable"]
    assert by_name["z.txt"]["expires_on"] is None
    o = by_name["Old"]
    assert (o["kind"], o["offload_id"], o["original_path"], o["restorable"]) == (
        "offload",
        off.id,
        "Docs/Old",
        False,
    )
    days = [e["day"] for e in body["entries"]]
    assert days == sorted(days, reverse=True)
    assert "stray.txt" not in by_name


def test_filters_and_paging(auth_client, env):
    acc, pair, _, _ = env
    unsynced = _list(auth_client, acc, pair="unsynced")["entries"]
    assert {e["name"] for e in unsynced} == {"x.txt", "z.txt"}  # neither has a pair now
    assert {e["kind"] for e in _list(auth_client, acc, kind="conflict")["entries"]} == {"conflict"}
    assert {e["name"] for e in _list(auth_client, acc, path="Docs/Sub")["entries"]} == {"a.txt"}
    page = _list(auth_client, acc, offset=2, limit=3)
    assert page["total"] == 8 and len(page["entries"]) == 3


def test_restore_puts_a_copy_back_and_queues_a_sync(auth_client, env, stub_queue, app):
    acc, pair, root, _ = env
    entries = {e["name"]: e["id"] for e in _list(auth_client, acc)["entries"]}
    r = auth_client.post(
        "/api/recovery/restore", json={"account_id": acc.id, "ids": [entries["a.txt"]]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["results"] == [
        {"id": entries["a.txt"], "restored_to": "Docs/Sub/a.txt", "error": None}
    ]
    assert (root / "Docs" / "Sub" / "a.txt").read_text() == "deleted"
    # A copy: the Recovery entry stays.
    assert entries["a.txt"] in {e["id"] for e in _list(auth_client, acc)["entries"]}
    assert stub_queue.requests == [(pair.id, "APP", "sync")]


def test_restore_never_overwrites(auth_client, env):
    acc, _, root, _ = env
    (root / "Docs" / "notes.txt").write_text("the kept version")
    entry = next(e["id"] for e in _list(auth_client, acc)["entries"] if e["name"] == "notes.txt")
    for expected in ("Docs/notes (restored).txt", "Docs/notes (restored 2).txt"):
        r = auth_client.post("/api/recovery/restore", json={"account_id": acc.id, "ids": [entry]})
        assert r.json()["results"][0]["restored_to"] == expected
    assert (root / "Docs" / "notes.txt").read_text() == "the kept version"
    assert (root / "Docs" / "notes (restored).txt").read_text() == "loser"


def test_restore_refusals_say_why(auth_client, env):
    acc, _, _, off = env
    entries = {e["name"]: e["id"] for e in _list(auth_client, acc)["entries"]}
    r = auth_client.post(
        "/api/recovery/restore",
        json={
            "account_id": acc.id,
            "ids": [entries["z.txt"], f"offload:{off.id}", "1/2026-10-01/nope.txt"],
        },
    )
    errors = [x["error"] for x in r.json()["results"]]
    assert "sync folder this came from was removed" in errors[0]
    assert "Restore to Drive" in errors[1]
    assert errors[2] == "not found"


def test_ids_cannot_leave_the_backups_folder(auth_client, env, app):
    acc, _, _, _ = env
    secret = app.state.layout.data / "secret.txt"
    secret.write_text("no")
    for bad in ("../../secret.txt", "1/2026-10-01/../../../secret.txt", "/etc/passwd"):
        r = auth_client.get("/api/recovery/file", params={"account_id": acc.id, "id": bad})
        assert r.status_code in (404, 422), bad
        res = auth_client.post("/api/recovery/restore", json={"account_id": acc.id, "ids": [bad]})
        assert res.json()["results"][0]["restored_to"] is None


def test_restore_is_refused_while_syncing_is_stopped(auth_client, env, stub_queue):
    acc, _, _, _ = env
    stub_queue.disabled_reason = MISSING_DISK_MSG
    r = auth_client.post("/api/recovery/restore", json={"account_id": acc.id, "ids": ["x"]})
    assert r.status_code == 503


def test_download_one_or_several(auth_client, env):
    acc, _, _, _ = env
    entries = {e["name"]: e["id"] for e in _list(auth_client, acc)["entries"]}
    r = auth_client.get("/api/recovery/file", params={"account_id": acc.id, "id": entries["b.txt"]})
    assert r.status_code == 200 and r.text == "first"
    assert "attachment" in r.headers["content-disposition"]
    r = auth_client.get(
        "/api/recovery/zip",
        params=[("account_id", acc.id), ("ids", entries["b.txt"]), ("ids", entries["c.txt"])],
    )
    assert r.status_code == 200
    names = zipfile.ZipFile(BytesIO(r.content)).namelist()
    assert sorted(names) == ["b.txt", "c.txt"]


def test_name_helpers():
    assert unconflict("notes.conflict2.txt") == "notes.txt"
    assert unconflict("a.tar.conflict1.gz") == "a.tar.gz"
    assert unconflict("README.conflict3") == "README"
    assert unconflict("plain.txt") == "plain.txt"
    names = restored_names("notes.txt")
    assert [next(names) for _ in range(3)] == [
        "notes.txt",
        "notes (restored).txt",
        "notes (restored 2).txt",
    ]


IN_OFFLOADED = (
    "This folder is offloaded: it's kept only on this server and doesn't sync with Google "
    "Drive. Restore it to Drive to add files here."
)


def _restore(client, acc, *ids):
    r = client.post("/api/recovery/restore", json={"account_id": acc.id, "ids": list(ids)})
    assert r.status_code == 200, r.text
    return r.json()


def test_offload_lists_and_manifests_are_not_entries(auth_client, env, app):
    acc, _, _, off = env
    offloads = app.state.layout.offloads_dir
    offloads.mkdir(parents=True, exist_ok=True)
    (offloads / "delete-12.txt").write_text("Old/a.txt\n")
    (offloads / f"{off.id}.json").write_text("[]")
    names = {e["name"] for e in _list(auth_client, acc)["entries"]}
    assert not {"delete-12.txt", f"{off.id}.json"} & names


def test_a_resync_backup_can_be_restored(auth_client, env):
    acc, _, root, _ = env
    entry = next(e["id"] for e in _list(auth_client, acc)["entries"] if e["name"] == "c.txt")
    assert _restore(auth_client, acc, entry)["results"][0]["restored_to"] == "Docs/c.txt"
    assert (root / "Docs" / "c.txt").read_text() == "resync"


def test_restore_into_an_offloaded_folder_is_refused(auth_client, env, app, stub_queue):
    acc, pair, root, _ = env
    b = app.state.layout.backups_dir / acc.email
    _put(b, f"{pair.id}/2026-10-05/.drivesync-deleted-121212-ef56/Old/deep/y.txt", "y")
    _put(b, f"{pair.id}/2026-10-05/Old/w.txt", "w")
    ids = [e["id"] for e in _list(auth_client, acc)["entries"] if e["name"] in ("y.txt", "w.txt")]
    assert len(ids) == 2
    body = _restore(auth_client, acc, *ids)
    assert [x["error"] for x in body["results"]] == [IN_OFFLOADED, IN_OFFLOADED]
    assert not (root / "Docs" / "Old").exists()  # not even the folder was made
    assert stub_queue.requests == [] and body["syncs"] == {}


def test_restore_next_to_an_offloaded_folder_still_works(auth_client, env, app):
    acc, pair, root, _ = env
    b = app.state.layout.backups_dir / acc.email
    _put(b, f"{pair.id}/2026-10-05/Older/y.txt", "y")
    entry = next(e["id"] for e in _list(auth_client, acc)["entries"] if e["name"] == "y.txt")
    assert _restore(auth_client, acc, entry)["results"][0]["restored_to"] == "Docs/Older/y.txt"


def test_restoring_an_item_deleted_outside_every_pair_is_local_only(auth_client, env, stub_queue):
    acc, _, root, _ = env
    entry = next(e["id"] for e in _list(auth_client, acc)["entries"] if e["name"] == "x.txt")
    body = _restore(auth_client, acc, entry)
    assert body["results"][0]["restored_to"] == "Loose/x.txt"
    assert (root / "Loose" / "x.txt").read_text() == "loose"
    assert stub_queue.requests == [] and body["syncs"] == {}


def test_restore_into_a_drive_to_here_pair_is_refused(auth_client, env, app, make_pair):
    acc, _, root, _ = env
    down = make_pair(
        acc, name="Down", local_subpath="Down", direction="REMOTE_TO_LOCAL", initialized=True
    )
    (root / "Down").mkdir()
    _put(app.state.layout.backups_dir / acc.email, f"{down.id}/2026-10-05/d.txt", "d")
    entry = next(e["id"] for e in _list(auth_client, acc)["entries"] if e["name"] == "d.txt")
    err = _restore(auth_client, acc, entry)["results"][0]["error"]
    assert "only receives changes from Google Drive" in err
    assert not (root / "Down" / "d.txt").exists()


def test_restore_leaves_no_half_copies_behind(auth_client, env):
    acc, _, root, _ = env
    entry = next(e["id"] for e in _list(auth_client, acc)["entries"] if e["name"] == "a.txt")
    _restore(auth_client, acc, entry)
    assert sorted(p.name for p in (root / "Docs" / "Sub").iterdir()) == ["a.txt"]


def test_links_and_odd_ids_are_refused(auth_client, env, app):
    acc, pair, _, _ = env
    b = app.state.layout.backups_dir / acc.email
    secret = app.state.layout.data / "secret.txt"
    secret.write_text("no")
    day = b / str(pair.id) / "2026-10-06"
    day.mkdir(parents=True)
    (day / "link.txt").symlink_to(secret)
    (day / "inner").symlink_to(b / str(pair.id) / "2026-10-01")  # stays inside, still a link
    names = {e["name"] for e in _list(auth_client, acc)["entries"]}
    assert "link.txt" not in names
    assert sum(1 for e in _list(auth_client, acc)["entries"] if e["name"] == "report.txt") == 1
    bad_ids = [
        f"{pair.id}/2026-10-06/link.txt",
        f"{pair.id}/2026-10-06/inner/report.txt",
        f"{pair.id}/2026-10-01/report.txt\x00",
        f"{pair.id}/not-a-day/report.txt",
        f"x{pair.id}/2026-10-01/report.txt",
        f"{pair.id}/2026-10-01",
    ]
    for bad in bad_ids:
        r = auth_client.get("/api/recovery/file", params={"account_id": acc.id, "id": bad})
        assert r.status_code in (404, 422), bad
        assert _restore(auth_client, acc, bad)["results"][0]["restored_to"] is None, bad
    r = auth_client.get("/api/recovery/zip", params=[("account_id", acc.id), ("ids", bad_ids[0])])
    assert r.status_code in (404, 422)


def test_another_accounts_backups_are_not_reachable(auth_client, env, app, make_account):
    acc, _, _, _ = env
    other = make_account("bob@example.com")
    _put(
        app.state.layout.backups_dir / other.email,
        "unsynced/2026-10-04/.drivesync-deleted-111111-cd34/bob.txt",
        "bob",
    )
    assert "bob.txt" not in {e["name"] for e in _list(auth_client, acc)["entries"]}
    rel = "unsynced/2026-10-04/.drivesync-deleted-111111-cd34/bob.txt"
    r = auth_client.get("/api/recovery/file", params={"account_id": acc.id, "id": rel})
    assert r.status_code == 404
    r = auth_client.get("/api/recovery/file", params={"account_id": other.id, "id": rel})
    assert r.status_code == 200 and r.text == "bob"


def test_unknown_account_is_404(auth_client, env):
    assert auth_client.get("/api/recovery", params={"account_id": 999}).status_code == 404


def test_zip_files_names_repeats_and_aborts_on_read_errors(tmp_path):
    from app.services.files.zipstream import zip_files

    a = tmp_path / "a"
    a.mkdir()
    (a / "same.txt").write_text("1")
    b = tmp_path / "b"
    b.mkdir()
    (b / "same.txt").write_text("2")
    data = b"".join(zip_files([(a / "same.txt", "same.txt"), (b / "same.txt", "same.txt")]))
    zf = zipfile.ZipFile(BytesIO(data))
    assert zf.namelist() == ["same.txt", "same (2).txt"]
    assert zf.read("same (2).txt") == b"2"
    with pytest.raises(OSError):
        b"".join(zip_files([(a / "same.txt", "same.txt"), (tmp_path / "gone.txt", "gone.txt")]))


def test_a_file_cleaned_up_before_download_is_a_plain_404(auth_client, env, monkeypatch):
    import app.api.recovery as api

    acc, _, _, _ = env
    entry = next(e["id"] for e in _list(auth_client, acc)["entries"] if e["name"] == "b.txt")
    real = api.resolve

    def resolve_then_prune(*args, **kwargs):
        found, src = real(*args, **kwargs)
        src.unlink()  # retention removed it in between
        return found, src

    monkeypatch.setattr(api, "resolve", resolve_then_prune)
    r = auth_client.get("/api/recovery/file", params={"account_id": acc.id, "id": entry})
    assert r.status_code == 404
    assert "no longer" in r.json()["detail"]


def test_restore_never_recreates_a_missing_sync_folder(auth_client, env, stub_queue):
    import shutil

    acc, _, root, _ = env
    entry = next(e["id"] for e in _list(auth_client, acc)["entries"] if e["name"] == "a.txt")
    shutil.rmtree(root / "Docs")  # as if the disk were not mounted
    body = _restore(auth_client, acc, entry)
    assert body["results"][0]["error"] == FOLDER_GONE_MSG
    assert not (root / "Docs").exists()
    assert stub_queue.requests == []


def test_restore_never_makes_folders_through_a_link_to_a_hidden_name(auth_client, env, app):
    acc, pair, root, _ = env
    (root / "Docs" / ".google-exports").mkdir()
    (root / "Docs" / "Sub").symlink_to(root / "Docs" / ".google-exports")
    entry = next(e["id"] for e in _list(auth_client, acc)["entries"] if e["name"] == "a.txt")
    _put(
        app.state.layout.backups_dir / acc.email,
        f"{pair.id}/2026-10-05/.drivesync-deleted-131313-aa11/Sub/new/q.txt",
        "q",
    )
    deep = next(e["id"] for e in _list(auth_client, acc)["entries"] if e["name"] == "q.txt")
    body = _restore(auth_client, acc, entry, deep)
    assert [x["restored_to"] for x in body["results"]] == [None, None]
    assert list((root / "Docs" / ".google-exports").iterdir()) == []


def test_kind_and_path_filters_are_checked(auth_client, env):
    acc, _, _, _ = env
    r = auth_client.get("/api/recovery", params={"account_id": acc.id, "kind": "nope"})
    assert r.status_code == 422
    r = auth_client.get("/api/recovery", params={"account_id": acc.id, "path": "Docs/../x"})
    assert r.status_code == 422
    assert {e["name"] for e in _list(auth_client, acc, path="/Docs//Sub/")["entries"]} == {"a.txt"}


def test_zip_repeats_use_the_same_naming_as_restore(tmp_path):
    from app.services.files.zipstream import zip_files

    a = tmp_path / "a"
    a.mkdir()
    (a / ".env").write_text("1")
    data = b"".join(zip_files([(a / ".env", ".env"), (a / ".env", ".env")]))
    assert zipfile.ZipFile(BytesIO(data)).namelist() == [".env", ".env (2)"]


def test_resolve_reports_when_an_entry_goes(env, app, db):
    from app.services.files.recovery import resolve
    from app.services.files.scope import load_scope

    acc, pair, _, _ = env
    scope = load_scope(db, app.state.layout, acc.id)
    loose = "unsynced/2026-10-04/.drivesync-deleted-111111-cd34/Loose/x.txt"
    entry, _ = resolve(app.state.layout, scope, loose, default_days=30)
    assert str(entry.expires_on) == "2026-11-04"
