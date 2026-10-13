import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.devtools.fake_gmail import make_mail
from app.models import MailJob, MailOffload, MailOffloadItem, RuleDecision
from app.models.enums import MailItemResult, MailPhase, MailState
from app.services.google.quota import ApiBudget
from app.services.mail.index import sync_state
from app.services.mail.offload import RESTARTED_RESTORE_MSG, RESTORE_SLOW_MSG
from app.services.mail.store import eml_path
from app.services.queue import MISSING_DISK_MSG
from app.services.rules.feedback import moved_back
from app.services.rules.jobs import RuleJobProblem, RuleJobs, RunScope
from app.services.rules.store import RuleIn, create_rule
from tests.mail_helpers import fake_adapter
from tests.offload_helpers import NOW
from tests.test_mail_api import OneAdapter

OLD = ("Old statement", "Old photos", "Ancient note")


async def _offloaded(client, box):
    await box.backup()
    r = client.post(
        "/api/mail/offloads", json={"account_id": box.acc.id, "q": "older_than:1y", "confirm": 3}
    )
    assert r.status_code == 202
    await box.run_queued()


def _restore(client, box, **body):
    return client.post("/api/mail/restore", json={"account_id": box.acc.id, **body})


def _imports(box):
    return [c for c in box.fake.calls if c[1].endswith("/messages/import")]


def _last_job(client):
    return client.get("/api/activity/jobs").json()[0]


async def test_restore_never_creates_a_duplicate(auth_client, offload_box, db):
    box = offload_box
    await _offloaded(auth_client, box)
    in_trash = box.row("Old statement")
    gone = box.row("Old photos")
    box.fake.remove(gone.provider_id)  # Gmail emptied its trash
    owner_put_back = box.row("Ancient note")
    box.fake.set_labels(owner_put_back.provider_id, ("INBOX",))  # history not seen yet
    r = _restore(auth_client, box, message_ids=[in_trash.id, gone.id, owner_put_back.id])
    assert r.status_code == 202 and r.json()["count"] == 3
    await box.run_queued()
    assert box.row("Old statement").provider_id == in_trash.provider_id  # untrashed, same id
    assert "TRASH" not in box.fake.messages[in_trash.provider_id].labels
    assert len(_imports(box)) == 1  # imported only the one Gmail no longer had
    new_id = box.row("Old photos").provider_id
    assert new_id != gone.provider_id and box.fake.messages[new_id].raw
    assert box.row("Old photos").file_path == gone.file_path  # the file stays where it is
    for s in OLD:
        assert box.row(s).state == MailState.IN_MAIL and box.row(s).offload_id is None
    copies = [m for m in box.fake.messages.values() if b"Subject: Old photos" in m.raw]
    assert len(copies) == 1
    # Asked again: everything is in Gmail already; nothing is imported or moved.
    r = _restore(auth_client, box, message_ids=[in_trash.id, gone.id])
    assert r.status_code == 409
    assert r.json()["detail"] == "Nothing here needs restoring: these mails are in Gmail."
    await box.backup()  # the next check sees the imported mail in Gmail's history
    assert len(box.rows_all()) == 6  # no second row for it
    assert box.row("Old photos").provider_id == new_id
    items = db.query(MailOffloadItem).all()
    assert {i.result for i in items} == {MailItemResult.RESTORED}


async def test_mail_restored_from_offloaded_is_not_offloaded_again_by_a_rule(
    auth_client, offload_box, db
):
    box = offload_box
    await _offloaded(auth_client, box)
    row, archived = box.row("Old statement"), box.row("Old photos")
    rule = create_rule(
        db,
        box.acc.id,
        RuleIn(name="Old", description="old mail", actions=[{"kind": "offload"}]),
        "a",
    )
    # Old statement was offloaded by the rule; Old photos only archived (and offloaded by hand).
    for mail, changes in ((row, {"trash": True}), (archived, {"remove": ["INBOX"]})):
        db.add(
            RuleDecision(
                account_id=box.acc.id,
                message_id=mail.id,
                rule_id=rule.id,
                status="applied",
                changes=changes,
            )
        )
    db.commit()
    _restore(auth_client, box, message_ids=[row.id, archived.id])
    await box.run_queued()
    db.expire_all()
    assert moved_back(db, row.id)  # new-mail sorting skips it
    assert not moved_back(db, archived.id)
    jobs = RuleJobs(box.app.state.session_factory, None, type("W", (), {"poke": lambda s: None})())
    scope = RunScope(message_ids=[row.id])
    with pytest.raises(RuleJobProblem, match="No mail in Gmail matches this search"):
        jobs.start_preview(box.acc.id, rule_ids=[rule.id], scope=scope)
    scope = RunScope(message_ids=[archived.id])
    assert jobs.start_preview(box.acc.id, rule_ids=[rule.id], scope=scope)


async def test_restore_by_import_puts_back_the_labels_that_still_exist(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old statement")  # labels: INBOX, Label_2 (Receipts)
    box.fake.remove(row.provider_id)
    r = _restore(auth_client, box, offload_id=row.offload_id)
    assert r.json()["count"] == 3
    await box.run_queued()
    new = box.row("Old statement").provider_id
    assert box.fake.messages[new].labels == ["INBOX", "Label_2"]


async def test_an_import_gmail_refuses_goes_again_with_the_basic_labels(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old statement")
    box.fake.remove(row.provider_id)
    box.fake.fail("POST", r"/messages/import$", 400)  # say, a label Gmail won't take
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    new = box.row("Old statement").provider_id
    assert box.fake.messages[new].labels == ["INBOX"]
    assert len(_imports(box)) == 2
    assert box.row("Old statement").state == MailState.IN_MAIL


async def test_a_big_mail_is_restored_with_a_resumable_upload(auth_client, offload_box):
    box = offload_box
    when = datetime.now(UTC) - timedelta(days=600)
    big = make_mail(
        "Big video",
        when=when,
        attachments=(("clip.mp4", "video/mp4", b"v" * (6 * 1024 * 1024)),),
    )
    box.fake.add(big, labels=("INBOX",), when=when)
    await box.backup()
    r = auth_client.post(
        "/api/mail/offloads", json={"account_id": box.acc.id, "q": "older_than:1y", "confirm": 4}
    )
    assert r.status_code == 202
    await box.run_queued()
    row = box.row("Big video")
    assert row.state == MailState.OFFLOADED
    box.fake.remove(row.provider_id)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    new = box.row("Big video").provider_id
    assert box.fake.messages[new].raw == big
    assert [c[2]["uploadType"] for c in _imports(box)] == ["resumable", "resumable"]


async def test_mail_deleted_in_gmail_is_restored_the_same_way(auth_client, offload_box):
    box = offload_box
    await box.backup()
    pid = box.provider_id("Last month")
    box.fake.set_labels(pid, ("TRASH",))  # the owner deleted it in Gmail
    await box.backup()
    assert box.row("Last month").state == MailState.DELETED
    r = _restore(auth_client, box, q="is:deleted")
    assert r.json()["count"] == 1
    await box.run_queued()
    assert box.row("Last month").state == MailState.IN_MAIL
    assert "TRASH" not in box.fake.messages[pid].labels


async def test_mail_in_gmails_spam_is_left_there(auth_client, offload_box):
    box = offload_box
    await box.backup()
    pid = box.provider_id("Last month")
    box.fake.set_labels(pid, ("SPAM",))  # the owner marked it as spam
    await box.backup()
    assert box.row("Last month").state == MailState.DELETED
    _restore(auth_client, box, q="is:deleted")
    await box.run_queued()
    assert box.row("Last month").state == MailState.DELETED
    assert box.fake.messages[pid].labels == ["SPAM"]
    assert _imports(box) == []
    job = _last_job(auth_client)
    assert job["state"] == "SUCCESS"
    assert job["message"] == (
        "Put 0 mails back in Gmail; 1 is in Gmail's Spam, so it was left there"
    )


async def test_restore_job_reports_what_happened(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    box.fake.remove(box.provider_id("Old photos"))
    _restore(auth_client, box, q="is:offloaded")
    await box.run_queued()
    job = _last_job(auth_client)
    assert job["kind"] == "RESTORE" and job["state"] == "SUCCESS"
    assert job["message"] == "Put 3 mails back in Gmail (2 out of Gmail's trash, 1 imported again)"


async def test_a_copy_that_does_not_check_out_is_never_imported(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    box.fake.remove(row.provider_id)
    path = eml_path(box.app.state.layout, box.acc.email, row.file_path)
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + b"!")  # same size, other contents
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    assert _imports(box) == []
    assert box.row("Old photos").state == MailState.OFFLOADED
    job = _last_job(auth_client)
    assert job["state"] == "FAILED"
    assert job["message"] == "Put 0 mails back in Gmail; 1 couldn't be put back (see the log)"


async def test_restore_is_refused_when_the_disk_is_missing_or_the_login_is_lost(
    auth_client, offload_box, app
):
    box = offload_box
    await _offloaded(auth_client, box)
    app.state.queue.disabled_reason = MISSING_DISK_MSG
    r = _restore(auth_client, box, q="is:offloaded")
    assert r.status_code == 503 and r.json()["detail"] == MISSING_DISK_MSG
    app.state.queue.disabled_reason = None
    box.fake.revoke()
    await box.backup()  # the check finds the login gone
    r = _restore(auth_client, box, q="is:offloaded")
    assert r.status_code == 409 and r.json()["detail"].startswith("Reconnect")
    assert box.queue.requests == []
    assert {box.row(s).state for s in OLD} == {MailState.OFFLOADED}


async def test_restore_is_refused_while_the_backup_checks_the_mailbox(auth_client, offload_box, db):
    box = offload_box
    await _offloaded(auth_client, box)
    sync_state(db, box.acc.id).phase = MailPhase.RECHECK
    db.commit()
    r = _restore(auth_client, box, q="is:offloaded")
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Wait until the mail backup has finished checking your mailbox, then try again."
    )
    assert box.queue.requests == []


async def test_a_bad_search_is_a_422(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    r = _restore(auth_client, box, q="older_than:soon")
    assert r.status_code == 422


async def test_a_restore_stopped_by_the_budget_pauses_and_goes_on(
    auth_client, offload_box, session_factory, db
):
    box = offload_box
    await _offloaded(auth_client, box)
    day = {"now": datetime.now(UTC)}
    # A mail in the trash costs 20 (state) + 5 (untrash) + 20 (state again): the first one
    # uses up the day's budget, the second one's first call is refused.
    budget = ApiBudget(session_factory, budget=lambda: 45, clock=lambda: day["now"])
    box.mail.adapters = OneAdapter(fake_adapter(box.fake, budget=budget))
    box.mail.budget = budget
    _restore(auth_client, box, q="is:offloaded")
    await box.run_queued()
    job = _last_job(auth_client)
    assert job["kind"] == "RESTORE" and job["state"] == "PAUSED"
    assert "It goes on after midnight UTC" in job["message"]
    states = [box.row(s).state for s in OLD]
    assert states.count(MailState.IN_MAIL) == 1
    await box.mail.tick()  # still the same day: nothing is asked for
    assert box.queue.requests == []
    day["now"] += timedelta(days=1)
    box.mail.budget = ApiBudget(session_factory, budget=lambda: 10_000, clock=lambda: day["now"])
    box.mail.adapters = OneAdapter(fake_adapter(box.fake))
    await box.mail.tick()
    await box.run_queued()
    assert {box.row(s).state for s in OLD} == {MailState.IN_MAIL}
    job = _last_job(auth_client)
    assert job["state"] == "SUCCESS" and job["message"].startswith("Put 2 mails back in Gmail")
    assert db.query(MailJob).filter_by(kind="RESTORE").count() == 2


async def test_a_rate_limited_restore_goes_on_the_next_minute(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    box.fake.fail("POST", r"/untrash$", 429, times=8)  # every try Google's client makes
    _restore(auth_client, box, q="is:offloaded")
    await box.run_queued()
    job = _last_job(auth_client)
    assert job["state"] == "PAUSED" and RESTORE_SLOW_MSG in job["message"]
    await box.mail.tick()
    await box.run_queued()
    assert {box.row(s).state for s in OLD} == {MailState.IN_MAIL}
    assert _last_job(auth_client)["state"] == "SUCCESS"


async def test_a_lost_login_stops_the_restore_and_asks_to_reconnect(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    _restore(auth_client, box, q="is:offloaded")
    box.fake.revoke()
    await box.run_queued()
    job = _last_job(auth_client)
    assert job["state"] == "FAILED" and "Reconnect" in job["message"]
    assert {box.row(s).state for s in OLD} == {MailState.OFFLOADED}


async def test_a_restore_that_finds_the_backup_checking_when_it_runs_changes_nothing(
    auth_client, offload_box, db
):
    box = offload_box
    await _offloaded(auth_client, box)
    assert _restore(auth_client, box, q="is:offloaded").status_code == 202
    sync_state(db, box.acc.id).phase = MailPhase.RECHECK  # a recheck started in between
    db.commit()
    await box.run_queued()
    job = _last_job(auth_client)
    assert job["state"] == "FAILED" and job["message"].endswith(
        "Wait until the mail backup has finished checking your mailbox, then try again."
    )
    assert {box.row(s).state for s in OLD} == {MailState.OFFLOADED}
    assert not any(c[1].endswith("/untrash") for c in box.fake.calls)


def _lose_first_import_answer(box):
    """Gmail stores the import, then its answer is lost on the way back."""
    real = box.fake.handle
    lost = {"n": 0}

    def lossy(request):
        resp = real(request)
        if request.url.path.endswith("/messages/import") and lost["n"] == 0:
            lost["n"] += 1
            return httpx.Response(503)
        return resp

    box.fake.handle = lossy


async def test_a_lost_import_answer_then_restore_again_never_makes_a_second_copy(
    auth_client, offload_box
):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    box.fake.remove(row.provider_id)
    _lose_first_import_answer(box)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    job = _last_job(auth_client)
    assert job["state"] == "FAILED" and "restore it again" in job["message"]
    assert box.row("Old photos").state == MailState.OFFLOADED
    r = _restore(auth_client, box, message_ids=[row.id])  # as the message says
    assert r.status_code == 202
    await box.run_queued()
    copies = [m for m in box.fake.messages.values() if b"Subject: Old photos" in m.raw]
    assert len(copies) == 1  # found by its Message-ID, never imported twice
    assert len(_imports(box)) == 1
    restored = box.row("Old photos")
    assert restored.state == MailState.IN_MAIL and restored.provider_id == copies[0].id
    assert _last_job(auth_client)["message"] == "Put 0 mails back in Gmail; 1 was already there"
    await box.backup()
    assert len(box.rows_all()) == 6


async def test_a_copy_found_in_gmails_trash_is_taken_out_of_it_not_imported(
    auth_client, offload_box
):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    box.fake.remove(row.provider_id)
    _lose_first_import_answer(box)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    (copy,) = [m for m in box.fake.messages.values() if b"Subject: Old photos" in m.raw]
    box.fake.set_labels(copy.id, ("TRASH",))
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    assert len(_imports(box)) == 1
    assert "TRASH" not in box.fake.messages[copy.id].labels
    assert box.row("Old photos").provider_id == copy.id
    assert _last_job(auth_client)["message"] == (
        "Put 1 mail back in Gmail (1 out of Gmail's trash)"
    )


async def test_two_paused_restores_of_one_account_both_go_on(auth_client, offload_box, db):
    box = offload_box
    await _offloaded(auth_client, box)
    a, b, c = (box.row(s).id for s in OLD)
    box.fake.fail("GET", r"/messages/", 429, times=8)  # every try Google's client makes
    _restore(auth_client, box, message_ids=[a, b])
    await box.run_queued()
    first = _last_job(auth_client)
    box.fake.fail("GET", r"/messages/", 429, times=8)
    _restore(auth_client, box, message_ids=[b, c])
    await box.run_queued()
    second = _last_job(auth_client)
    assert first["state"] == second["state"] == "PAUSED"
    await box.mail.tick()
    restores = [r for r in box.queue.requests if r[1] == "restore"]
    assert len(restores) == 1
    assert sorted(restores[0][2]["message_ids"]) == sorted([a, b, c])  # merged, b once
    await box.run_queued()
    assert {box.row(s).state for s in OLD} == {MailState.IN_MAIL}
    jobs = {j["id"]: j for j in auth_client.get("/api/activity/jobs").json()}
    for old in (first, second):  # Activity no longer shows them as waiting
        assert jobs[old["id"]]["state"] == "INTERRUPTED"
        assert "It went on as restore job" in jobs[old["id"]]["message"]
    assert _last_job(auth_client)["state"] == "SUCCESS"


def test_a_paused_restore_ends_failed_at_startup(settings, make_account, db):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.models.enums import JobState, MailJobKind
    from app.services.mail.jobs import finish_mail_job, start_mail_job
    from tests.test_mail_api import StubMailQueue

    first = create_app(settings, start_background=False)
    acc = make_account()
    paused = start_mail_job(
        db, first.state.layout, first.state.bus, acc.id, MailJobKind.RESTORE, "MANUAL", 3
    )
    finish_mail_job(db, first.state.bus, paused.id, JobState.PAUSED, "Put 1 mail back in Gmail.")
    running = start_mail_job(
        db, first.state.layout, first.state.bus, acc.id, MailJobKind.RESTORE, "MANUAL", 3
    )
    app = create_app(settings)  # background services on, as in production
    app.state.mail.queue = StubMailQueue()
    with TestClient(app):
        db.expire_all()
        for job_id in (paused.id, running.id):
            row = db.get(MailJob, job_id)
            assert row.state == JobState.FAILED
            assert row.message.endswith(RESTARTED_RESTORE_MSG)
        assert app.state.mail.queue.requests == []  # a restore is not run again by itself


async def test_a_search_answer_that_is_another_mail_is_never_adopted(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    box.fake.remove(row.provider_id)
    other = box.fake.add(make_mail("Stranger"))  # in Gmail, with no row here
    box.fake.loose_matches = [other]  # Gmail's search answers it for this Message-ID too
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    new = box.row("Old photos").provider_id
    assert len(_imports(box)) == 1 and new not in (other, row.provider_id)
    assert b"Subject: Old photos" in box.fake.messages[new].raw
    assert "TRASH" not in box.fake.messages[other].labels  # the stranger was never touched


async def test_a_mail_with_an_odd_message_id_is_imported_without_a_search(auth_client, offload_box):
    box = offload_box
    when = datetime.now(UTC) - timedelta(days=700)
    raw = make_mail("Odd id", when=when)
    raw = re.sub(rb"(?m)^Message-ID: .*$", b"Message-ID: <x}{in:inbox@example.com>", raw)
    box.fake.add(raw, labels=("INBOX",), when=when)
    await box.backup()
    r = auth_client.post(
        "/api/mail/offloads", json={"account_id": box.acc.id, "q": "older_than:1y", "confirm": 4}
    )
    assert r.status_code == 202
    await box.run_queued()
    row = box.row("Odd id")
    box.fake.remove(row.provider_id)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    assert not any("rfc822msgid" in c[2].get("q", "") for c in box.fake.calls)
    assert len(_imports(box)) == 1
    assert box.row("Odd id").state == MailState.IN_MAIL


async def test_a_copy_found_in_spam_is_adopted_and_left_there(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    box.fake.remove(row.provider_id)
    _lose_first_import_answer(box)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    (copy,) = [m for m in box.fake.messages.values() if b"Subject: Old photos" in m.raw]
    box.fake.set_labels(copy.id, ("SPAM",))
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    after = box.row("Old photos")
    assert after.provider_id == copy.id  # known by the copy's id now
    assert after.state == MailState.OFFLOADED and after.offload_id == row.offload_id
    assert box.fake.messages[copy.id].labels == ["SPAM"]
    assert len(_imports(box)) == 1
    assert _last_job(auth_client)["message"].endswith("1 is in Gmail's Spam, so it was left there")


def test_with_the_disk_missing_a_paused_restore_still_ends_failed_at_startup(
    settings, make_account, db
):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.models.enums import JobState, MailJobKind
    from app.services.mail.jobs import finish_mail_job, start_mail_job
    from tests.test_mail_api import StubMailQueue

    first = create_app(settings, start_background=False)  # writes the volume file
    acc = make_account()
    paused = start_mail_job(
        db, first.state.layout, first.state.bus, acc.id, MailJobKind.RESTORE, "MANUAL", 3
    )
    finish_mail_job(db, first.state.bus, paused.id, JobState.PAUSED, "Put 1 mail back in Gmail.")
    first.state.layout.volume_file.unlink()
    app = create_app(settings)
    app.state.mail.queue = StubMailQueue()
    with TestClient(app):
        assert app.state.queue.disabled_reason == MISSING_DISK_MSG
        db.expire_all()
        row = db.get(MailJob, paused.id)
        assert row.state == JobState.FAILED and row.message.endswith(RESTARTED_RESTORE_MSG)
        assert app.state.mail.queue.requests == []


async def test_recovery_lists_mail_offloads_and_mail_deleted_in_gmail(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    # A mail the owner trashes in Gmail by hand counts as deleted there; offloaded ones do not.
    box.fake.set_labels(box.provider_id("Last week"), ("TRASH",))
    await box.backup()
    body = auth_client.get("/api/recovery/mail", params={"account_id": box.acc.id}).json()
    assert body["deleted_count"] == 1
    (off,) = body["offloads"]
    assert off["done"] == 3 and off["google_emptied"] is False
    assert "emptyable" not in off


async def test_recovery_leaves_out_offloads_that_moved_nothing_and_checks_the_account(
    auth_client, offload_box, db
):
    box = offload_box
    await box.backup()
    db.add(MailOffload(account_id=box.acc.id, filter_text="x", state="failed", total=2, done=0))
    db.commit()
    body = auth_client.get("/api/recovery/mail", params={"account_id": box.acc.id}).json()
    assert body == {"offloads": [], "deleted_count": 0}
    assert auth_client.get("/api/recovery/mail", params={"account_id": 999}).status_code == 404


async def test_recovery_pages_offloads_newest_first(auth_client, offload_box, db):
    box = offload_box
    ids = []
    for n in range(5):
        o = MailOffload(account_id=box.acc.id, filter_text=f"f{n}", state="done", total=1, done=1)
        db.add(o)
        db.commit()
        ids.append(o.id)
    url, p = "/api/recovery/mail", {"account_id": box.acc.id}
    first = auth_client.get(url, params={**p, "limit": 2}).json()["offloads"]
    second = auth_client.get(url, params={**p, "limit": 2, "offset": 2}).json()["offloads"]
    assert [o["id"] for o in first + second] == ids[::-1][:4]


def _copies(box, subject: str) -> list:
    return [m for m in box.fake.messages.values() if f"Subject: {subject}".encode() in m.raw]


async def test_a_check_between_a_lost_import_answer_and_restore_again_adopts_the_copy(
    auth_client, offload_box
):
    """Gmail took the import but its answer was lost; the 5-minute check then sees the
    imported mail before the owner restores again. It belongs to the offloaded mail's row:
    one copy in Gmail, one row here."""
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    box.fake.remove(row.provider_id)
    _lose_first_import_answer(box)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    assert box.row("Old photos").import_started_at is not None
    await box.backup()  # the check runs before the owner asks again
    (copy,) = _copies(box, "Old photos")
    (after,) = [r for r in box.rows_all() if r.subject == "Old photos"]
    assert after.id == row.id and after.provider_id == copy.id
    assert after.state == MailState.IN_MAIL and after.offload_id is None
    assert after.import_started_at is None
    assert after.file_path == row.file_path  # the file stays where it is
    files = list(box.mail.layout.mail_dir(box.acc.email).rglob(f"{copy.id}.eml"))
    assert files == []  # no second file for it either
    r = _restore(auth_client, box, message_ids=[row.id])
    assert r.status_code == 409  # nothing to restore: it is in Gmail
    assert len(_copies(box, "Old photos")) == 1 and len(_imports(box)) == 1
    assert len(box.rows_all()) == 6


async def test_restore_again_merges_a_row_the_check_made_for_its_own_import(
    auth_client, offload_box, monkeypatch
):
    """Should a check still have saved the lost import as a new row, restore again finds
    the copy owned by that newer row and merges the two: no second import, one row."""
    from app.services.mail import backup as backup_mod

    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    box.fake.remove(row.provider_id)
    _lose_first_import_answer(box)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    with monkeypatch.context() as m:
        m.setattr(backup_mod, "adopt_restored_copy", lambda *a, **k: None)
        await box.backup()
    (r2,) = [r for r in box.rows_all() if r.subject == "Old photos" and r.id != row.id]
    r2_file = eml_path(box.mail.layout, box.acc.email, r2.file_path)
    assert r2_file.exists()
    r = _restore(auth_client, box, message_ids=[row.id])
    assert r.status_code == 202
    await box.run_queued()
    (copy,) = _copies(box, "Old photos")
    (after,) = [r for r in box.rows_all() if r.subject == "Old photos"]
    assert after.id == row.id and after.provider_id == copy.id
    assert after.state == MailState.IN_MAIL and after.import_started_at is None
    assert after.file_path == row.file_path
    assert len(_imports(box)) == 1 and len(box.rows_all()) == 6
    with box.app.state.session_factory() as db:
        left = db.execute(text("SELECT rowid FROM mail_fts WHERE rowid = :i"), {"i": r2.id})
        assert left.all() == []  # no search row left for the other
    assert not r2_file.exists()  # its file was a second copy of the same mail
    assert eml_path(box.mail.layout, box.acc.email, row.file_path).exists()


async def test_a_merge_keeps_the_other_rows_file_when_its_bytes_differ(
    auth_client, offload_box, monkeypatch, caplog
):
    """Never lose a mail file: the second row's file is removed only when it holds exactly
    the same bytes as the kept copy; otherwise it stays (unlisted) and the log says so."""
    from app.services.mail import backup as backup_mod

    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    box.fake.remove(row.provider_id)
    _lose_first_import_answer(box)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    (copy,) = _copies(box, "Old photos")
    copy.raw = b"X-Added-By-Gmail: yes\r\n" + copy.raw  # Gmail's copy is not byte for byte
    with monkeypatch.context() as m:
        m.setattr(backup_mod, "adopt_restored_copy", lambda *a, **k: None)
        await box.backup()
    (r2,) = [r for r in box.rows_all() if r.subject == "Old photos" and r.id != row.id]
    r2_file = eml_path(box.mail.layout, box.acc.email, r2.file_path)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    (after,) = [r for r in box.rows_all() if r.subject == "Old photos"]
    assert after.id == row.id and after.provider_id == copy.id
    assert r2_file.read_bytes() == copy.raw  # kept, not listed
    assert eml_path(box.mail.layout, box.acc.email, row.file_path).exists()
    assert "kept an unlisted copy" in caplog.text


async def test_a_merge_keeps_the_other_file_when_the_kept_one_does_not_check_out(
    auth_client, offload_box, monkeypatch, caplog
):
    """The second copy is removed only while the kept file is whole: if the kept file is
    damaged or missing, the identical second copy may be the only good one."""
    from app.services.mail import backup as backup_mod

    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    box.fake.remove(row.provider_id)
    _lose_first_import_answer(box)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    with monkeypatch.context() as m:
        m.setattr(backup_mod, "adopt_restored_copy", lambda *a, **k: None)
        await box.backup()
    (r2,) = [r for r in box.rows_all() if r.subject == "Old photos" and r.id != row.id]
    r2_file = eml_path(box.mail.layout, box.acc.email, r2.file_path)
    eml_path(box.mail.layout, box.acc.email, row.file_path).write_bytes(b"damaged")
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    assert [r.id for r in box.rows_all() if r.subject == "Old photos"] == [row.id]
    assert r2_file.exists()  # the kept file didn't check out: the second copy stays
    assert "kept an unlisted copy" in caplog.text


async def test_a_successful_import_leaves_no_mark(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    box.fake.remove(row.provider_id)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    assert box.row("Old photos").import_started_at is None


async def test_a_restore_of_more_ids_than_sqlite_takes_in_one_query_works(auth_client, offload_box):
    """SQLite takes at most 32,766 values in one query; a big pick is asked in chunks."""
    from app.services.mail.offload import restore_targets
    from app.services.mail.offload_plan import Pick

    box = offload_box
    await _offloaded(auth_client, box)
    old = [box.row(s) for s in ("Ancient note", "Old photos", "Old statement")]  # oldest first
    ids = [*range(100_000, 140_000), *(r.id for r in old), box.row("Yesterday").id]
    assert box.offloads._restore_done(box.acc.id, ids) == [
        i for i in ids if i not in {r.id for r in old}
    ]
    threads = [*(f"t{i}" for i in range(40_000)), *(r.thread_id for r in old)]
    with box.app.state.session_factory() as db:
        assert restore_targets(db, box.acc.id, Pick(message_ids=ids), None, NOW) == [
            r.id for r in old
        ]
        assert restore_targets(db, box.acc.id, Pick(thread_ids=threads), None, NOW) == [
            r.id for r in old
        ]


async def test_restoring_marks_a_rules_decision_with_the_service_clock(
    auth_client, offload_box, db
):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old statement")
    rule = create_rule(
        db,
        box.acc.id,
        RuleIn(name="Old", description="old mail", actions=[{"kind": "offload"}]),
        "a",
    )
    d = RuleDecision(
        account_id=box.acc.id,
        message_id=row.id,
        rule_id=rule.id,
        status="applied",
        changes={"trash": True},
    )
    db.add(d)
    db.commit()
    fixed = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
    box.offloads.clock = lambda: fixed
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    db.expire_all()
    assert db.get(RuleDecision, d.id).moved_back_at.replace(tzinfo=UTC) == fixed


async def test_restore_of_the_kept_view_honours_the_label(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    r = _restore(auth_client, box, view="kept", label="Label_2")  # only "Old statement" has it
    assert r.status_code == 202 and r.json()["count"] == 1
    await box.run_queued()
    assert box.row("Old statement").state == MailState.IN_MAIL
    assert box.row("Old photos").state == MailState.OFFLOADED
    assert box.row("Ancient note").state == MailState.OFFLOADED


async def test_restore_of_the_kept_view_with_a_search_and_the_old_archive_name(
    auth_client, offload_box
):
    box = offload_box
    await _offloaded(auth_client, box)
    r = _restore(auth_client, box, view="kept", q="photos")
    assert r.status_code == 202 and r.json()["count"] == 1
    await box.run_queued()
    r = _restore(auth_client, box, view="archive", q="older_than:1y")  # an old link's name
    assert r.status_code == 202 and r.json()["count"] == 2  # the two still out of Gmail


@pytest.mark.parametrize("view", ["all", "inbox", "archived", "suggested"])
async def test_a_gmail_view_with_a_search_or_a_label_restores_nothing(
    auth_client, offload_box, view
):
    """Only Offloaded, Deleted in Gmail and the kept view restore."""
    box = offload_box
    await _offloaded(auth_client, box)
    for body in (
        {"q": "older_than:1y"},
        {"label": "Label_2"},
        {"q": "statement", "label": "Label_2"},
    ):
        r = _restore(auth_client, box, view=view, **body)
        assert r.status_code == 409, (view, body, r.text)
        assert r.json()["detail"] == "Nothing here needs restoring: these mails are in Gmail."
    assert {box.row(s).state for s in OLD} == {MailState.OFFLOADED}
