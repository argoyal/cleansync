import asyncio
from datetime import UTC, datetime, timedelta

from app.models import MailJob, MailOffload, MailOffloadItem
from app.models.enums import (
    MailItemResult,
    MailOffloadState,
    MailPhase,
    MailState,
    ServiceKind,
    ServiceState,
)
from app.services.accounts.services import service_state
from app.services.app_settings import set_settings
from app.services.google.quota import ApiBudget
from app.services.mail.index import sync_state
from app.services.mail.offload import BACKUP_CHECKING_MSG
from app.services.mail.queue import MailQueue
from app.services.mail.store import eml_path
from tests.mail_helpers import fake_adapter
from tests.test_mail_api import OneAdapter

OLD = ("Old statement", "Old photos", "Ancient note")


def _start(client, acc, confirm, **body):
    return client.post(
        "/api/mail/offloads", json={"account_id": acc.id, "confirm": confirm, **body}
    )


def _trash_calls(box):
    return [c for c in box.fake.calls if c[0] == "POST" and c[1].endswith("/trash")]


async def test_an_offload_moves_mail_to_gmails_trash_checks_it_and_marks_it(
    auth_client, offload_box, db
):
    box = offload_box
    await box.backup()
    r = _start(auth_client, box.acc, 3, q="older_than:1y")
    assert r.status_code == 202, r.text
    off_id = r.json()["offload_id"]
    assert box.queue.requests[-1] == (box.acc.id, "offload", {"offload_id": off_id}, "MANUAL")
    detail = auth_client.get(f"/api/mail/offloads/{off_id}").json()
    assert detail["state"] == "WAITING" and detail["total"] == 3
    await box.run_queued()
    detail = auth_client.get(f"/api/mail/offloads/{off_id}").json()
    assert detail["state"] == "DONE" and detail["done"] == 3 and detail["skipped"] == 0
    assert detail["bytes"] == sum(box.row(s).size for s in OLD) and detail["not_done"] == 0
    assert detail["google_emptied"] is False and detail["skipped_items"] == []
    for s in OLD:
        row = box.row(s)
        assert row.state == MailState.OFFLOADED and row.offload_id == off_id
        assert "TRASH" in box.fake.messages[row.provider_id].labels
    assert box.row("Last month").state == MailState.IN_MAIL
    assert len(_trash_calls(box)) == 3
    listed = auth_client.get("/api/mail/offloads", params={"account_id": box.acc.id}).json()
    assert [o["id"] for o in listed] == [off_id]
    jobs = auth_client.get("/api/activity/jobs").json()
    assert jobs[0]["kind"] == "OFFLOAD" and jobs[0]["state"] == "SUCCESS"
    assert jobs[0]["message"].startswith("Offloaded 3 of 3 mails")


async def test_a_mail_is_trashed_only_after_its_local_file_checks_out(auth_client, offload_box):
    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    bad = box.row("Old photos")
    path = eml_path(box.app.state.layout, box.acc.email, bad.file_path)
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + b"!")  # same size, other contents: only the SHA-256 tells
    await box.run_queued()
    off = box.row("Old statement").offload_id
    detail = auth_client.get(f"/api/mail/offloads/{off}").json()
    assert detail["state"] == "DONE_WITH_SKIPS" and detail["done"] == 2 and detail["skipped"] == 1
    assert detail["skipped_items"][0]["subject"] == "Old photos"
    assert detail["skipped_items"][0]["reason"] == (
        "The copy on this server didn't check out (the file's contents changed), "
        "so it stayed in Gmail."
    )
    assert not any(bad.provider_id in c[1] for c in _trash_calls(box))  # never sent to the trash
    assert box.row("Old photos").state == MailState.IN_MAIL
    assert "TRASH" not in box.fake.messages[bad.provider_id].labels


async def test_a_missing_file_is_never_trashed(auth_client, offload_box):
    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    gone = box.row("Ancient note")
    eml_path(box.app.state.layout, box.acc.email, gone.file_path).unlink()
    await box.run_queued()
    assert not any(gone.provider_id in c[1] for c in _trash_calls(box))
    assert box.row("Ancient note").state == MailState.IN_MAIL
    assert box.row("Old statement").state == MailState.OFFLOADED


async def test_offload_never_includes_mail_newer_than_the_minimum_age(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    p = auth_client.post(
        "/api/mail/offloads/preview", json={"account_id": box.acc.id, "q": "older_than:30d"}
    ).json()
    assert p["count"] == 4  # Old statement, Old photos, Ancient note, Last month (40 days)
    assert _start(auth_client, box.acc, 4, q="older_than:30d").status_code == 202
    set_settings(db, {"mail_min_age_days": 60})  # changed before the run
    await box.run_queued()
    last_month = box.row("Last month")
    assert last_month.state == MailState.IN_MAIL
    assert not any(last_month.provider_id in c[1] for c in _trash_calls(box))
    off = db.get(MailOffload, box.row("Old statement").offload_id)
    assert off.skipped == 1 and off.state == MailOffloadState.DONE_WITH_SKIPS
    item = db.query(MailOffloadItem).filter_by(message_id=last_month.id).one()
    assert item.reason == "It is newer than 60 days, so it stayed in Gmail."
    for s in ("Last week", "Yesterday"):
        assert box.row(s).state == MailState.IN_MAIL


async def test_the_count_must_match_and_be_typed(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    r = _start(auth_client, box.acc, 2, q="older_than:1y")
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "The number of mails changed since the preview (now 3). Check the preview again."
    )
    r = _start(auth_client, box.acc, 0, q="has:attachment")
    assert r.status_code == 409 and r.json()["detail"] == "Nothing to offload with this filter."
    assert box.queue.requests == []
    assert db.query(MailOffload).count() == 0


async def test_an_offload_is_refused_while_the_backup_checks_the_mailbox(
    auth_client, offload_box, db
):
    box = offload_box
    await box.backup()
    sync_state(db, box.acc.id).phase = MailPhase.RECHECK
    db.commit()
    r = _start(auth_client, box.acc, 3, q="older_than:1y")
    assert r.status_code == 409 and r.json()["detail"] == BACKUP_CHECKING_MSG
    assert box.queue.requests == [] and db.query(MailOffload).count() == 0


async def test_a_waiting_offload_waits_while_the_backup_checks_the_mailbox(
    auth_client, offload_box, db
):
    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    sync_state(db, box.acc.id).phase = MailPhase.RECHECK  # a re-check began meanwhile
    db.commit()
    await box.run_queued()
    off = db.query(MailOffload).one()
    db.refresh(off)
    assert off.state == MailOffloadState.WAITING and off.reason == BACKUP_CHECKING_MSG
    assert _trash_calls(box) == [] and db.query(MailJob).filter_by(kind="OFFLOAD").count() == 0
    sync_state(db, box.acc.id).phase = MailPhase.INCREMENTAL
    db.commit()
    await box.mail.tick()
    await box.run_queued()
    db.refresh(off)
    assert off.state == MailOffloadState.DONE and off.done == 3


async def test_an_offload_waits_behind_a_running_mail_job(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    gate = asyncio.Event()
    order: list[str] = []

    async def handler(account_id, action, args, trigger):
        order.append(action)
        if action == "backup":
            await gate.wait()  # a check that takes a while
        await box.mail.handle(account_id, action, args, trigger)

    queue = MailQueue(handler, is_stopped=box.mail.stopped)
    box.mail.queue = queue
    queue.request(box.acc.id, "backup", trigger="SCHEDULE")
    await asyncio.sleep(0)
    assert queue.is_running(box.acc.id)
    r = _start(auth_client, box.acc, 3, q="older_than:1y")
    assert r.status_code == 202, r.text
    off_id = r.json()["offload_id"]
    await asyncio.sleep(0.05)
    assert queue.waiting(box.acc.id) == [("offload", {"offload_id": off_id})]
    assert _trash_calls(box) == []  # nothing left Gmail while the check ran
    gate.set()
    await queue.wait_idle()
    assert order == ["backup", "offload"]
    assert db.get(MailOffload, off_id).state == MailOffloadState.DONE


async def test_an_offload_stopped_by_the_budget_waits_and_goes_on(
    auth_client, offload_box, session_factory, db
):
    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    day = {"now": datetime.now(UTC)}
    budget = ApiBudget(session_factory, budget=lambda: 30, clock=lambda: day["now"])
    box.mail.adapters = OneAdapter(fake_adapter(box.fake, budget=budget))
    box.mail.budget = budget
    # One mail costs 5 (trash) + 20 (state check): the second mail's state check goes over the
    # line, after its trash call. It stays pending and is checked again when the offload goes on.
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.WAITING and off.done == 1
    assert "It goes on after midnight UTC" in off.reason
    jobs = auth_client.get("/api/activity/jobs").json()
    assert jobs[0]["kind"] == "OFFLOAD" and jobs[0]["state"] == "PAUSED"
    await box.mail.tick()  # still the same day: nothing is asked for
    assert box.queue.requests == []
    day["now"] += timedelta(days=1)
    box.mail.budget = ApiBudget(session_factory, budget=lambda: 10_000, clock=lambda: day["now"])
    box.mail.adapters = OneAdapter(fake_adapter(box.fake))
    await box.mail.tick()  # the next day's tick asks for it again
    assert (box.acc.id, "offload", {"offload_id": off.id}) in [r[:3] for r in box.queue.requests]
    await box.run_queued()
    db.refresh(off)
    assert off.state == MailOffloadState.DONE and off.done == 3 and off.skipped == 0
    assert {box.row(s).state for s in OLD} == {MailState.OFFLOADED}


async def test_a_stop_during_the_run_fails_it_and_leaves_the_rest_in_gmail(
    auth_client, offload_box, db, app
):
    box = offload_box
    await box.backup()
    _start(auth_client, box.acc, 3, q="older_than:1y")
    from app.services.queue import MISSING_DISK_MSG

    app.state.queue.disabled_reason = MISSING_DISK_MSG
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.FAILED and off.reason == MISSING_DISK_MSG
    detail = auth_client.get(f"/api/mail/offloads/{off.id}").json()
    assert detail["not_done"] == 3
    assert _trash_calls(box) == []  # nothing left Gmail
    items = db.query(MailOffloadItem).filter_by(offload_id=off.id).all()
    assert {i.result for i in items} == {MailItemResult.PENDING}


async def test_a_mail_gmail_refuses_to_trash_is_skipped_and_the_rest_go_on(
    auth_client, offload_box, db
):
    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    pid = box.row("Old statement").provider_id
    box.fake.fail("POST", rf"/messages/{pid}/trash$", 400)
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.DONE_WITH_SKIPS and off.done == 2 and off.skipped == 1
    item = db.query(MailOffloadItem).filter_by(message_id=box.row("Old statement").id).one()
    assert item.reason.startswith("Gmail didn't move it to the trash (")
    assert box.row("Old statement").state == MailState.IN_MAIL


async def test_a_lost_login_fails_the_offload_and_asks_to_reconnect(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    box.fake.revoke()
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.FAILED and off.reason
    assert service_state(db, box.acc.id, ServiceKind.MAIL) == ServiceState.NEEDS_RECONNECT
    assert {box.row(s).state for s in OLD} == {MailState.IN_MAIL}


async def test_a_failing_tick_hook_does_not_stop_the_tick(offload_box):
    box = offload_box
    seen = []

    def broken():
        raise RuntimeError("boom")

    box.mail.tick_hooks.insert(0, broken)
    box.mail.tick_hooks.append(lambda: seen.append("after"))
    await box.mail.tick()
    assert seen == ["after"]
