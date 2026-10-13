"""Delete forever on mail in Gmail (bulk offload spec §5): Gmail first, then CleanSync's
copy, as a background job."""

from sqlalchemy import select

from app.models import Account, Event, MailOffload
from app.models.enums import MailOffloadState, MailState
from app.services.accounts.permanent import NOT_ALLOWED_MSG, permission_lost
from app.services.events.bus import EventBus
from app.services.mail.store import eml_path
from tests.test_mail_bulk_offload import allow_permanent
from tests.test_mail_offload_run import OLD

URL = "/api/mail/delete-forever"


def _forever(auth_client, box, confirm, **body):
    return auth_client.post(URL, json={"account_id": box.acc.id, "confirm": confirm, **body})


async def test_it_removes_the_mail_from_gmail_then_the_copy(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    rows = {s: box.row(s) for s in OLD}
    r = _forever(auth_client, box, 3, view="all", q="older_than:1y")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 0 and body["left"] == 0 and body["job_id"]
    await box.run_queued()
    layout = box.app.state.layout
    for s, row in rows.items():
        assert row.provider_id not in box.fake.messages
        assert box.row(s) is None
        assert not eml_path(layout, box.acc.email, row.file_path).exists()
    d = auth_client.get(f"/api/mail/offloads/{body['job_id']}").json()
    assert d["kind"] == "delete_forever" and d["state"] == "DONE" and d["done"] == 3
    ev = db.scalar(select(Event).where(Event.kind == "mail_deleted_forever"))
    assert ev.message == "Deleted 3 mails forever from Gmail" and len(ev.data["mails"]) == 3
    # Not an offload: not in the Batches tab, and its job has its own kind.
    assert auth_client.get("/api/mail/offloads", params={"account_id": box.acc.id}).json() == []
    jobs = auth_client.get("/api/activity/jobs").json()
    assert jobs[0]["kind"] == "DELETE_FOREVER" and jobs[0]["message"].startswith(
        "Deleted 3 of 3 mails forever"
    )


async def test_a_mail_gmail_wont_delete_keeps_its_copy(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    bad = box.row("Old statement")
    box.fake.fail("POST", r"/batchDelete$", 400)
    box.fake.fail("DELETE", rf"/messages/{bad.provider_id}$", 400)
    job_id = _forever(auth_client, box, 3, view="all", q="older_than:1y").json()["job_id"]
    await box.run_queued()
    kept = box.row("Old statement")
    assert kept is not None and kept.state == MailState.IN_MAIL
    assert eml_path(box.app.state.layout, box.acc.email, kept.file_path).is_file()
    assert bad.provider_id in box.fake.messages
    assert box.row("Old photos") is None and box.row("Ancient note") is None
    d = auth_client.get(f"/api/mail/offloads/{job_id}").json()
    assert d["state"] == "DONE_WITH_SKIPS" and (d["done"], d["skipped"]) == (2, 1)
    assert d["skipped_items"][0]["reason"].startswith("Gmail didn't delete it (")


async def test_it_is_refused_without_permanent_delete(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    r = _forever(auth_client, box, 3, view="all", q="older_than:1y")
    assert r.status_code == 400 and r.json()["detail"] == NOT_ALLOWED_MSG
    assert db.query(MailOffload).count() == 0


async def test_the_typed_count_must_match_and_one_tick_cant_take_the_mailbox(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    r = _forever(auth_client, box, 2, view="all", q="older_than:1y")
    assert r.status_code == 400
    assert r.json()["detail"] == "The number you typed doesn't match the 3 mails picked."
    r = _forever(auth_client, box, 6, view="all", q="")
    assert r.status_code == 400
    assert r.json()["detail"] == "Choose at least one filter, or pick mails in the list."
    assert db.query(MailOffload).count() == 0


async def test_one_picked_recent_mail_has_no_minimum_age(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    row = box.row("Last week")
    r = _forever(auth_client, box, 1, view="inbox", message_ids=[row.id])
    assert r.status_code == 200, r.text
    await box.run_queued()
    assert box.row("Last week") is None and row.provider_id not in box.fake.messages


async def test_a_permission_lost_before_it_runs_fails_it_and_keeps_everything(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    job_id = _forever(auth_client, box, 3, view="all", q="older_than:1y").json()["job_id"]
    permission_lost(db, EventBus(), box.acc.id)
    await box.run_queued()
    off = db.get(MailOffload, job_id)
    db.refresh(off)
    assert off.state == MailOffloadState.FAILED and off.reason.startswith(NOT_ALLOWED_MSG)
    assert "What wasn't deleted yet stays in Gmail" in off.reason
    assert all(box.row(s) is not None for s in OLD)
    assert all(box.row(s).provider_id in box.fake.messages for s in OLD)


async def test_google_refusing_the_permission_mid_job_stops_it_and_turns_it_off(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    _forever(auth_client, box, 3, view="all", q="older_than:1y")
    box.fake.fail("POST", r"/batchDelete$", 403, reason="insufficientPermissions")
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.FAILED and off.done == 0
    assert all(box.row(s) is not None for s in OLD)
    acc = db.get(Account, box.acc.id)
    db.refresh(acc)
    assert acc.permanent_delete is False
