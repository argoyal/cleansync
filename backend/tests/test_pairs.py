from app.core.clock import utcnow
from app.services.catalog.model import Catalog


def _create(auth_client, account_id, **kw):
    body = {"account_id": account_id, "name": "Docs"} | kw
    return auth_client.post("/api/sync-pairs", json=body)


def test_create_pair_uses_defaults_and_makes_folder(auth_client, app, make_account):
    acc = make_account()
    r = _create(auth_client, acc.id, local_subpath="Documents", remote_path="/Documents/")
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["remote_path"] == "Documents"
    assert p["schedule_interval_s"] == 300
    assert p["max_deletions"] == 10
    assert p["backups_enabled"] is True and p["backup_retention_days"] == 30
    assert p["status"] == "NEW" and p["initialized"] is False
    assert p["local_path"].endswith("storage/alice@example.com/Documents")
    assert (app.state.layout.storage / "alice@example.com" / "Documents").is_dir()
    filters = app.state.layout.pair_filters(p["id"]).read_text()
    assert "- /.google-exports/**" in filters


def test_local_path_cannot_escape_account_folder(auth_client, make_account):
    acc = make_account()
    assert _create(auth_client, acc.id, local_subpath="../bob@example.com").status_code == 422
    assert _create(auth_client, acc.id, remote_path="../x").status_code == 422


def test_overlapping_pairs_are_rejected(auth_client, make_account):
    acc = make_account()
    assert _create(auth_client, acc.id, local_subpath="Work", remote_path="Work").status_code == 201
    r = _create(auth_client, acc.id, local_subpath="Work/Sub", remote_path="Other")
    assert r.status_code == 422 and "overlaps" in r.json()["detail"]
    r = _create(auth_client, acc.id, local_subpath="Else", remote_path="Work/Deep")
    assert r.status_code == 422
    assert (
        _create(auth_client, acc.id, local_subpath="Workbench", remote_path="Workbench").status_code
        == 201
    )


def test_whole_drive_pair_overlaps_everything(auth_client, make_account):
    acc = make_account()
    assert _create(auth_client, acc.id).status_code == 201
    assert _create(auth_client, acc.id, local_subpath="A", remote_path="A").status_code == 422


def test_invalid_schedule_and_retention_rejected(auth_client, make_account):
    acc = make_account()
    assert _create(auth_client, acc.id, schedule_interval_s=42).status_code == 422
    assert _create(auth_client, acc.id, backup_retention_days=3).status_code == 422
    assert _create(auth_client, acc.id, max_deletions=-1).status_code == 422


def test_manual_only_schedule_allowed(auth_client, make_account):
    acc = make_account()
    r = _create(auth_client, acc.id, schedule_interval_s=None)
    assert r.status_code == 201 and r.json()["schedule_interval_s"] is None


def test_update_and_list(auth_client, make_account):
    acc = make_account()
    pid = _create(auth_client, acc.id).json()["id"]
    r = auth_client.put(f"/api/sync-pairs/{pid}", json={"max_deletions": 50, "name": "Docs 2"})
    assert r.json()["max_deletions"] == 50 and r.json()["name"] == "Docs 2"
    assert [p["id"] for p in auth_client.get("/api/sync-pairs").json()] == [pid]


def test_delete_pair_keeps_files(auth_client, app, make_account):
    acc = make_account()
    pid = _create(auth_client, acc.id, local_subpath="Keep").json()["id"]
    f = app.state.layout.storage / "alice@example.com" / "Keep" / "a.txt"
    f.write_text("x")
    app.state.catalog.store.save(Catalog.build(pid, utcnow(), "root", []))
    assert auth_client.delete(f"/api/sync-pairs/{pid}").status_code == 204
    assert f.exists()
    assert app.state.catalog.load(pid) is None  # the one shared store was evicted
    assert auth_client.get(f"/api/sync-pairs/{pid}").status_code == 404


def test_pause_and_resume(auth_client, make_account):
    acc = make_account()
    pid = _create(auth_client, acc.id).json()["id"]
    assert auth_client.post(f"/api/sync-pairs/{pid}/pause").json()["status"] == "PAUSED"
    assert auth_client.post(f"/api/sync-pairs/{pid}/resume").json()["status"] == "NEW"


def test_overlap_is_case_and_unicode_insensitive(auth_client, make_account):
    acc = make_account()
    assert _create(auth_client, acc.id, local_subpath="Work", remote_path="Work").status_code == 201
    r = _create(auth_client, acc.id, local_subpath="work", remote_path="Other")
    assert r.status_code == 422 and "overlaps" in r.json()["detail"]
    assert _create(auth_client, acc.id, local_subpath="Café", remote_path="C1").status_code == 201
    assert _create(auth_client, acc.id, local_subpath="Café", remote_path="C2").status_code == 422


def test_null_settings_are_kept(auth_client, make_account):
    acc = make_account()
    r = _create(auth_client, acc.id, local_subpath="A", remote_path="A", backup_retention_days=None)
    assert r.status_code == 201 and r.json()["backup_retention_days"] is None
    pid = _create(auth_client, acc.id, local_subpath="B", remote_path="B").json()["id"]
    r = auth_client.put(f"/api/sync-pairs/{pid}", json={"schedule_interval_s": None})
    assert r.json()["schedule_interval_s"] is None and r.json()["backup_retention_days"] == 30
    r = auth_client.put(f"/api/sync-pairs/{pid}", json={"backup_retention_days": None})
    assert r.json()["backup_retention_days"] is None and r.json()["schedule_interval_s"] is None
    r = auth_client.put(f"/api/sync-pairs/{pid}", json={"name": "x"})
    assert r.json()["backup_retention_days"] is None


def test_minus_one_rejected(auth_client, make_account):
    acc = make_account()
    assert _create(auth_client, acc.id, schedule_interval_s=-1).status_code == 422
    assert _create(auth_client, acc.id, backup_retention_days=-1).status_code == 422


def test_resume_does_not_mask_problems(auth_client, db, make_account, make_pair):
    acc = make_account()
    cases = [
        ("BLOCKED", "ATTENTION"),
        ("AUTH", "ATTENTION"),
        ("FAILED", "ERROR"),
        ("ACCESS_CHECK", "ERROR"),
        (None, "NEW"),
    ]
    for i, (reason, expected) in enumerate(cases):
        pair = make_pair(
            acc,
            name=f"P{i}",
            local_subpath=f"p{i}",
            remote_path=f"p{i}",
            attention_reason=reason,
            last_error="boom" if reason else None,
        )
        assert auth_client.post(f"/api/sync-pairs/{pair.id}/pause").json()["status"] == "PAUSED"
        paused = auth_client.get(f"/api/sync-pairs/{pair.id}").json()
        assert paused["attention_reason"] == reason and paused["last_error"] == pair.last_error
        assert auth_client.post(f"/api/sync-pairs/{pair.id}/resume").json()["status"] == expected


def test_google_exports_folder_reserved(auth_client, make_account):
    acc = make_account()
    assert _create(auth_client, acc.id, local_subpath=".google-exports").status_code == 422
    assert _create(auth_client, acc.id, local_subpath=".google-exports/x").status_code == 422


# I3: deleting a pair must not leave state that a later pair could pick up.
def test_deleted_pair_id_is_never_reused_and_previews_are_removed(auth_client, app, make_account):
    acc = make_account()
    pid = _create(auth_client, acc.id, local_subpath="A", remote_path="A").json()["id"]
    previews = app.state.layout.previews_dir
    for name in (f"{pid}.json", f"{pid}-different.txt", f"{pid}-initial-different.txt"):
        (previews / name).write_text("{}")
    (previews / f"{pid}0.json").write_text("{}")  # another pair's preview stays
    assert auth_client.delete(f"/api/sync-pairs/{pid}").status_code == 204
    assert sorted(p.name for p in previews.iterdir()) == [f"{pid}0.json"]
    new_id = _create(auth_client, acc.id, local_subpath="A", remote_path="A").json()["id"]
    assert new_id != pid
    assert app.state.initial.load_preview(new_id) is None


def test_job_ids_are_never_reused(db, make_account, make_pair):
    from app.models import SyncJob

    acc = make_account()
    pair = make_pair(acc)
    job = SyncJob(pair_id=pair.id, kind="BISYNC", trigger="MANUAL")
    db.add(job)
    db.commit()
    old = job.id
    db.delete(job)
    db.commit()
    again = SyncJob(pair_id=pair.id, kind="BISYNC", trigger="MANUAL")
    db.add(again)
    db.commit()
    assert again.id > old


def test_delete_is_refused_while_the_pair_is_running_or_queued(auth_client, app, make_account):
    acc = make_account()
    pid = _create(auth_client, acc.id).json()["id"]
    queue = app.state.queue
    queue._running.add(pid)
    try:
        assert auth_client.delete(f"/api/sync-pairs/{pid}").status_code == 409
    finally:
        queue._running.discard(pid)
    queue._pending[pid] = ("sync", "MANUAL")
    try:
        assert auth_client.delete(f"/api/sync-pairs/{pid}").status_code == 409
    finally:
        queue._pending.pop(pid)
    assert auth_client.delete(f"/api/sync-pairs/{pid}").status_code == 204
