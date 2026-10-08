import asyncio
import json
import time
from datetime import timedelta

from app.core.clock import utcnow
from app.models import Account, SyncJob
from app.models.enums import AccountStatus, JobKind, JobState
from app.services.catalog.model import Catalog
from app.services.scheduler import Scheduler
from tests.fake_rclone import jl


def _line(path, *, is_dir=False, size=3, id_=None, mime="text/plain", starred=None):
    item = {
        "Path": path,
        "Name": path.rsplit("/", 1)[-1],
        "Size": size,
        "MimeType": mime,
        "ModTime": "2026-10-08T08:00:00Z",
        "IsDir": is_dir,
    }
    if id_:
        item["ID"] = id_
    if starred is not None:
        item["Metadata"] = {"starred": starred}
    return json.dumps(item) + ","


def _listing(*lines):
    # rclone's real layout: "[", one object per line with a trailing comma, "]".
    last = lines[-1].rstrip(",") if lines else None
    return ["[", *lines[:-1], *([last] if last else []), "]"]


async def test_refresh_lists_drive_and_saves_the_catalog(
    catalog_service, fake_rclone, make_account, make_pair
):
    acc = make_account()
    pair = make_pair(acc)
    fake_rclone.on(
        "lsjson",
        match=["--recursive"],
        stdout=_listing(
            _line("Work", is_dir=True, id_="W1"),
            _line("Work/a.txt", id_="A1", starred="true"),
            _line("Report.docx", size=-1, id_="R1"),
        ),
    )
    cat = await catalog_service.refresh(pair.id)
    assert set(cat.files) == {"Work", "Work/a.txt"}
    assert cat.files["Work/a.txt"].starred and cat.natives["Report.docx"].native == "document"
    assert cat.root_id == "root"
    assert catalog_service.load(pair.id).files["Work"].id == "W1"
    call = fake_rclone.calls()[-1]
    assert call[4:6] == ["lsjson", f"{acc.remote_name}:"]
    for flag in ("--recursive", "--fast-list", "--metadata", "--drive-show-all-gdocs"):
        assert flag in call


async def test_subfolder_pair_records_its_folder_id(
    catalog_service, fake_rclone, make_account, make_pair
):
    acc = make_account()
    pair = make_pair(acc, remote_path="Work/Docs")
    fake_rclone.on("lsjson", match=["--recursive"], stdout=_listing(_line("a.txt")))
    fake_rclone.on("lsjson", match=["--stat"], stdout=['{"Path":"","IsDir":true,"ID":"DOCS1"}'])
    cat = await catalog_service.refresh(pair.id)
    assert cat.root_id == "DOCS1"
    assert fake_rclone.calls()[0][5] == f"{acc.remote_name}:Work/Docs"


async def test_missing_drive_folder_means_an_empty_listing(
    catalog_service, fake_rclone, make_account, make_pair
):
    pair = make_pair(make_account(), remote_path="New")
    fake_rclone.on(
        "lsjson",
        stderr=[jl("error listing: directory not found", level="error")],
        exit=3,
    )
    cat = await catalog_service.refresh(pair.id)
    assert cat is not None and cat.files == {} and cat.root_id is None


async def test_auth_failure_keeps_the_old_listing_and_flags_the_account(
    catalog_service, fake_rclone, make_account, make_pair, db
):
    acc = make_account()
    pair = make_pair(acc)
    fake_rclone.on("lsjson", match=["--recursive"], stdout=_listing(_line("a.txt")))
    await catalog_service.refresh(pair.id)
    fake_rclone.on(
        "lsjson", stderr=[jl("couldn't fetch token: invalid_grant", level="error")], exit=1
    )
    plan = json.loads(fake_rclone.plan_file.read_text())
    plan["lsjson"] = plan["lsjson"][1:]  # only the failing rule is left
    fake_rclone.plan_file.write_text(json.dumps(plan))
    cat = await catalog_service.refresh(pair.id)
    assert set(cat.files) == {"a.txt"}
    assert "couldn't fetch token" in catalog_service.last_error[pair.id]
    db.expire_all()
    assert db.get(Account, acc.id).status == AccountStatus.NEEDS_REAUTH


async def test_unreadable_listing_is_not_saved(
    catalog_service, fake_rclone, make_account, make_pair
):
    pair = make_pair(make_account())
    fake_rclone.on("lsjson", stdout=["[", '{"Path": "a.txt", "Size": 1,', "]"])
    assert await catalog_service.refresh(pair.id) is None
    assert catalog_service.load(pair.id) is None
    assert catalog_service.last_error[pair.id]


async def test_disconnected_account_is_not_listed(
    catalog_service, fake_rclone, make_account, make_pair, db
):
    acc = make_account()
    pair = make_pair(acc)
    acc.status = AccountStatus.DISCONNECTED
    db.commit()
    assert await catalog_service.refresh(pair.id) is None
    assert fake_rclone.calls() == []


async def test_two_refreshes_at_once_list_drive_once(
    catalog_service, fake_rclone, make_account, make_pair
):
    pair = make_pair(make_account())
    fake_rclone.on("lsjson", stdout=_listing(_line("a.txt")), sleep=0.5)
    a, b = await asyncio.gather(catalog_service.refresh(pair.id), catalog_service.refresh(pair.id))
    assert a is not None and b is not None
    assert len(fake_rclone.calls()) == 1


async def test_listeners_and_events_fire_after_a_refresh(
    catalog_service, fake_rclone, make_account, make_pair
):
    pair = make_pair(make_account())
    seen = []
    catalog_service.listeners.append(seen.append)
    await catalog_service.refresh(pair.id)
    assert seen == [pair.id]


def _job(db, pair, kind=JobKind.BISYNC, **counts):
    job = SyncJob(pair_id=pair.id, trigger="MANUAL", kind=kind, state=JobState.SUCCESS, **counts)
    db.add(job)
    db.commit()
    return job


async def test_finished_jobs_that_changed_files_trigger_one_refresh(
    catalog_service, make_account, make_pair, db
):
    pair = make_pair(make_account())
    asked = []
    catalog_service.request_refresh = asked.append
    catalog_service.store.save(Catalog.build(pair.id, utcnow(), "root", []))
    _job(db, pair, files_changed=0)
    catalog_service.on_job_finished(pair.id)
    assert asked == []  # nothing changed
    _job(db, pair, files_changed=2, files_downloaded=2)
    catalog_service.on_job_finished(pair.id)
    catalog_service.on_job_finished(pair.id)  # the same job again (e.g. a dry run after it)
    assert asked == [pair.id]
    _job(db, pair, kind=JobKind.RESYNC)
    catalog_service.on_job_finished(pair.id)
    assert asked == [pair.id, pair.id]


async def test_refresh_stale_only_lists_old_listings_of_active_pairs(
    catalog_service, fake_rclone, make_account, make_pair, db
):
    acc = make_account()
    fresh = make_pair(acc, name="fresh", local_subpath="f", remote_path="f")
    old = make_pair(acc, name="old", local_subpath="o", remote_path="o")
    make_pair(acc, name="paused", local_subpath="p", remote_path="p", paused=True)
    catalog_service.store.save(Catalog.build(fresh.id, utcnow(), "x", []))
    catalog_service.store.save(Catalog.build(old.id, utcnow() - timedelta(hours=2), "x", []))
    await catalog_service.refresh_stale()
    listed = [c[5] for c in fake_rclone.calls() if "--recursive" in c]
    assert listed == [f"{acc.remote_name}:o"]  # not the fresh one, not the paused one


def test_catalog_api_summary_and_refresh(auth_client, app, make_account, fake_rclone):
    acc = make_account()
    fake_rclone.on("lsjson", match=["--recursive"], stdout=_listing(_line("a.txt", size=7)))
    pair = auth_client.post("/api/sync-pairs", json={"account_id": acc.id, "name": "D"}).json()
    # creating the pair started a listing in the background; wait for it
    for _ in range(100):
        r = auth_client.get(f"/api/sync-pairs/{pair['id']}/catalog").json()
        if r["refreshed_at"] and not r["refreshing"]:
            break
        time.sleep(0.05)
    assert r["files"] == 1 and r["bytes"] == 7 and r["error"] is None
    r = auth_client.post(f"/api/sync-pairs/{pair['id']}/catalog/refresh")
    assert r.status_code == 202
    assert auth_client.get("/api/sync-pairs/999/catalog").status_code == 404


async def test_scheduler_adds_the_catalog_job(session_factory, layout, catalog_service):
    s = Scheduler(
        session_factory=session_factory,
        queue=None,
        accounts=None,
        layout=layout,
        catalog=catalog_service,
    )
    s.start()
    try:
        assert s.aps.get_job("catalogs").trigger.interval == timedelta(minutes=10)
    finally:
        s.shutdown()
