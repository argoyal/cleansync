# backend/tests/test_mail_bulk_offload.py
"""Offload in bulk (bulk offload spec §4): batches of up to 1,000 mails, one Gmail call
each, the copy here checked first."""

import pytest
from sqlalchemy import select

from app.models import Account, Event, MailMessage, MailOffload, MailOffloadItem
from app.models.enums import MailItemResult, MailOffloadState, MailState, ServiceKind, ServiceState
from app.services.accounts.oauth import FULL_MAIL_SCOPE
from app.services.accounts.permanent import LOST_MSG
from app.services.accounts.services import service_state
from app.services.google.client import RateLimited
from app.services.google.quota import ApiBudget
from app.services.mail import offload as offload_mod
from app.services.mail.store import eml_path
from tests.mail_helpers import fake_adapter
from tests.test_mail_api import OneAdapter
from tests.test_mail_offload_run import OLD, _start


def allow_permanent(box, db):
    acc = db.get(Account, box.acc.id)
    acc.scopes = f"{acc.scopes} {FULL_MAIL_SCOPE}".strip()
    acc.permanent_delete = True
    db.commit()


class Recording:
    """The real adapter, remembering the ids of each batch call."""

    def __init__(self, inner):
        self.inner, self.sent = inner, []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def batch_trash(self, ids):
        self.sent.append(("trash", list(ids)))
        await self.inner.batch_trash(ids)

    async def batch_delete(self, ids):
        self.sent.append(("delete", list(ids)))
        await self.inner.batch_delete(ids)


class LostAnswer(Recording):
    """Gmail does the first batch, but CleanSync never records it: the run pauses right after
    (a restart does the same, see Task 5)."""

    def __init__(self, inner):
        super().__init__(inner)
        self.lost = False

    async def _then_lose(self):
        if not self.lost:
            self.lost = True
            raise RateLimited()

    async def batch_trash(self, ids):
        await super().batch_trash(ids)
        await self._then_lose()

    async def batch_delete(self, ids):
        await super().batch_delete(ids)
        await self._then_lose()


def _use(box, adapter):
    box.mail.adapters = OneAdapter(adapter)
    return adapter


def _posts(box, name):
    return [c for c in box.fake.calls if c[0] == "POST" and c[1].endswith(f"/{name}")]


def _detail(auth_client, off_id):
    return auth_client.get(f"/api/mail/offloads/{off_id}").json()


async def _offload_old(auth_client, box):
    await box.backup()
    r = _start(auth_client, box.acc, 3, q="older_than:1y")
    assert r.status_code == 202, r.text
    await box.run_queued()
    return r.json()["offload_id"]


async def test_with_permanent_delete_one_call_removes_the_batch_for_good(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    off_id = await _offload_old(auth_client, box)
    assert len(_posts(box, "batchDelete")) == 1 and _posts(box, "batchModify") == []
    assert not [c for c in box.fake.calls if c[1].endswith("/trash")]
    for s in OLD:
        row = box.row(s)
        assert row.state == MailState.OFFLOADED and row.offload_id == off_id
        assert row.provider_id not in box.fake.messages
    d = _detail(auth_client, off_id)
    assert d["state"] == "DONE" and d["kind"] == "offload"
    assert (d["done"], d["removed"], d["trashed"]) == (3, 3, 0)


async def test_without_it_one_call_moves_the_batch_to_the_trash(auth_client, offload_box):
    box = offload_box
    off_id = await _offload_old(auth_client, box)
    assert len(_posts(box, "batchModify")) == 1 and _posts(box, "batchDelete") == []
    assert all("TRASH" in box.fake.messages[box.row(s).provider_id].labels for s in OLD)
    d = _detail(auth_client, off_id)
    assert (d["done"], d["removed"], d["trashed"]) == (3, 0, 3)


async def test_batches_hold_at_most_the_batch_size(auth_client, offload_box, monkeypatch):
    monkeypatch.setattr(offload_mod, "GMAIL_BATCH", 2)
    box = offload_box
    await box.backup()
    rec = _use(box, Recording(box.mail.adapters.adapter))
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    await box.run_queued()
    assert [len(ids) for _, ids in rec.sent] == [2, 1]


async def test_a_copy_that_fails_is_skipped_and_never_sent(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    bad = box.row("Old photos")
    path = eml_path(box.app.state.layout, box.acc.email, bad.file_path)
    path.write_bytes(path.read_bytes()[:-1] + b"!")  # same size, other contents
    rec = _use(box, Recording(box.mail.adapters.adapter))
    await box.run_queued()
    sent = [pid for _, ids in rec.sent for pid in ids]
    assert bad.provider_id not in sent and len(sent) == 2
    assert bad.provider_id in box.fake.messages
    assert box.row("Old photos").state == MailState.IN_MAIL
    off = db.query(MailOffload).one()
    assert (off.done, off.skipped) == (2, 1)
    item = db.query(MailOffloadItem).filter_by(message_id=bad.id).one()
    assert item.reason == (
        "The copy on this server didn't check out (the file's contents changed), "
        "so it stayed in Gmail."
    )


async def test_a_refused_batch_goes_one_at_a_time_and_only_the_bad_mail_stays(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    bad = box.row("Old statement").provider_id
    box.fake.fail("POST", r"/batchDelete$", 400)
    box.fake.fail("DELETE", rf"/messages/{bad}$", 400)
    await box.run_queued()
    assert box.row("Old statement").state == MailState.IN_MAIL and bad in box.fake.messages
    for s in ("Old photos", "Ancient note"):
        assert box.row(s).state == MailState.OFFLOADED
        assert box.row(s).provider_id not in box.fake.messages
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.DONE_WITH_SKIPS
    assert (off.done, off.skipped, off.removed) == (2, 1, 2)
    item = db.query(MailOffloadItem).filter_by(result=MailItemResult.SKIPPED).one()
    assert item.reason.startswith("Gmail didn't delete it (")


async def test_in_the_fallback_a_mail_gmail_no_longer_has_counts_as_offloaded(
    auth_client, offload_box, db
):
    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    box.fake.remove(box.row("Ancient note").provider_id)  # deleted in Gmail meanwhile
    await box.run_queued()  # batchModify refuses the unknown id; one at a time, trash says 404
    assert {box.row(s).state for s in OLD} == {MailState.OFFLOADED}
    off = db.query(MailOffload).one()
    assert (off.done, off.skipped, off.removed) == (3, 0, 1)


async def test_gmails_server_trouble_is_tried_again_after_a_pause(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    pauses: list[float] = []

    async def nap(s):
        pauses.append(s)

    box.offloads.sleep = nap
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    box.fake.fail("POST", r"/batchDelete$", 503, times=2)
    await box.run_queued()
    assert pauses == [5.0, 20.0]
    assert db.query(MailOffload).one().done == 3


async def test_server_trouble_that_lasts_pauses_the_offload_and_the_next_run_counts_each_mail_once(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)

    async def nap(s):
        return None

    box.offloads.sleep = nap
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    box.fake.fail("POST", r"/batchDelete$", 503, times=3)
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.WAITING and off.done == 0
    assert off.reason == "Gmail didn't answer; CleanSync will try again."
    results = {i.result for i in db.query(MailOffloadItem)}
    assert results == {MailItemResult.PENDING}
    for s_ in OLD:  # Gmail did it though it never answered
        box.fake.remove(box.row(s_).provider_id)
    await box.mail.tick()
    await box.run_queued()
    db.refresh(off)
    assert off.state == MailOffloadState.DONE and (off.done, off.skipped, off.removed) == (3, 0, 3)


async def test_a_budget_with_less_than_one_batch_call_left_is_not_resumed(
    auth_client, offload_box, db, session_factory
):
    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    box.queue.requests.clear()  # the start's own request
    budget = ApiBudget(session_factory, budget=lambda: 100)
    box.mail.budget = budget
    budget.spend(51)  # 49 left: a batch call costs 50
    box.offloads.resume_waiting()
    assert box.queue.requests == []
    box.mail.budget = ApiBudget(session_factory, budget=lambda: 100)
    box.mail.budget.spend(50)  # 50 left
    box.offloads.resume_waiting()
    assert [r[1] for r in box.queue.requests] == ["offload"]


async def test_mark_offloaded_skips_a_mail_that_was_deleted_forever_meanwhile(
    auth_client, offload_box, db
):
    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    off = db.query(MailOffload).one()
    item = db.query(MailOffloadItem).filter_by(offload_id=off.id).first()
    row = db.get(MailMessage, item.message_id)
    box.offloads._mark_offloaded(off.id, [(item.id, row.id), (999_999, 999_999)], removed=True)
    db.refresh(off)
    assert (off.done, off.skipped, off.removed) == (1, 1, 1)


async def test_a_permission_lost_inside_the_fallback_trashes_and_gives_the_trash_reason(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    bad = box.row("Old photos").provider_id  # the second of the three, oldest first
    box.fake.fail("POST", r"/batchDelete$", 400)
    box.fake.fail("DELETE", rf"/messages/{bad}$", 403, reason="insufficientPermissions")
    box.fake.fail("POST", rf"/messages/{bad}/trash$", 400)
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert (off.done, off.skipped, off.removed) == (2, 1, 1)
    item = db.query(MailOffloadItem).filter_by(result=MailItemResult.SKIPPED).one()
    assert item.reason.startswith("Gmail didn't move it to the trash (")
    acc = db.get(Account, box.acc.id)
    db.refresh(acc)
    assert acc.permanent_delete is False and acc.permanent_delete_note == LOST_MSG
    assert "TRASH" in box.fake.messages[box.row("Old statement").provider_id].labels


async def test_quota_is_counted_per_call_not_per_mail(
    auth_client, offload_box, db, session_factory
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    budget = ApiBudget(session_factory, budget=lambda: 10_000)
    box.mail.adapters = OneAdapter(fake_adapter(box.fake, budget=budget))
    box.mail.budget = budget
    await box.run_queued()
    assert budget.used_today() == 50  # one batchDelete for the three mails


@pytest.mark.parametrize("permanent", [True, False])
async def test_a_batch_gmail_did_but_cleansync_never_recorded_is_sent_again_and_counted_once(
    auth_client, offload_box, db, permanent
):
    box = offload_box
    if permanent:
        allow_permanent(box, db)
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    _use(box, LostAnswer(box.mail.adapters.adapter))
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.WAITING and off.done == 0
    await box.backup()  # a check sees them gone (or in the trash) before the offload goes on
    assert {box.row(s).state for s in OLD} == {MailState.DELETED}
    await box.mail.tick()
    await box.run_queued()
    db.refresh(off)
    assert off.state == MailOffloadState.DONE and off.skipped == 0
    assert (off.done, off.removed) == (3, 3 if permanent else 0)
    assert {box.row(s).state for s in OLD} == {MailState.OFFLOADED}


async def test_when_google_refuses_the_full_permission_the_batch_goes_to_the_trash(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    box.fake.fail("POST", r"/batchDelete$", 403, reason="insufficientPermissions")
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.DONE and (off.done, off.removed) == (3, 0)
    assert all("TRASH" in box.fake.messages[box.row(s).provider_id].labels for s in OLD)
    acc = db.get(Account, box.acc.id)
    db.refresh(acc)
    assert acc.permanent_delete is False and acc.permanent_delete_note == LOST_MSG
    assert service_state(db, box.acc.id, ServiceKind.MAIL) == ServiceState.ACTIVE
    assert db.scalar(select(Event).where(Event.kind == "permanent_delete_lost")) is not None


async def test_restore_after_a_permanent_delete_imports_the_mail_again(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    off_id = await _offload_old(auth_client, box)
    r = auth_client.post("/api/mail/restore", json={"account_id": box.acc.id, "offload_id": off_id})
    assert r.status_code == 202, r.text
    await box.run_queued()
    assert {box.row(s).state for s in OLD} == {MailState.IN_MAIL}
    jobs = auth_client.get("/api/activity/jobs").json()
    assert jobs[0]["message"] == "Put 3 mails back in Gmail (3 imported again)"
