"""Mail panel spec §8: one "removed from Gmail" state. A rule's Offload and Offload on one
mail reach it through the applier; the one restore path leaves it."""

import pytest
from sqlalchemy import select

from app.devtools.fake_gmail import FakeGmail, make_mail
from app.models import MailMessage, RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, MailState, ServiceKind, ServiceState
from app.services.accounts.services import ensure_services, set_service_state
from app.services.google.quota import BudgetReached
from tests.mail_helpers import fake_adapter
from tests.rules_helpers import keep
from tests.test_mail_api import OneAdapter
from tests.test_mail_restore import _copies, _lose_first_import_answer, _offloaded, _restore


async def test_a_rules_offload_ends_offloaded_and_restore_brings_it_back(
    auth_client, offload_box, app
):
    box = offload_box
    await box.backup()
    row = box.row("Old photos")
    with app.state.session_factory() as db:
        d = RuleDecision(
            account_id=box.acc.id,
            message_id=row.id,
            rule_name="Junk",
            how=DecisionHow.AI,
            actions=[{"kind": "offload"}],
            labels_seen=list(row.labels),
            status=DecisionStatus.SUGGESTED,
        )
        db.add(d)
        db.commit()
        did = d.id
    assert await app.state.rule_applier.apply(did) == "applied"
    after = box.row("Old photos")
    assert after.state == MailState.OFFLOADED and after.offload_id is None
    assert "TRASH" in box.fake.messages[row.provider_id].labels
    listed = auth_client.get(
        "/api/mail/threads", params={"account_id": box.acc.id, "view": "offloaded"}
    ).json()["threads"]
    assert [t["subject"] for t in listed] == ["Old photos"]
    # Not part of any offload batch: the Batches tab lists only filter and pick offloads.
    assert auth_client.get("/api/mail/offloads", params={"account_id": box.acc.id}).json() == []
    assert _restore(auth_client, box, message_ids=[row.id]).status_code == 202
    await box.run_queued()
    assert box.row("Old photos").state == MailState.IN_MAIL
    assert "TRASH" not in box.fake.messages[row.provider_id].labels
    back = box.row("Old photos")
    assert "INBOX" in back.labels and "TRASH" not in back.labels


async def test_offload_on_one_mail_goes_through_the_applier(auth_client, offload_box):
    box = offload_box
    await box.backup()
    row = box.row("Yesterday")  # like Delete before it, one mail by hand isn't held back by age
    url = f"/api/mail/messages/{row.id}/offload"
    r = auth_client.post(url, json={"account_id": box.acc.id})
    assert r.status_code == 200 and r.json() == {"offloaded": True, "reason": None}
    assert box.row("Yesterday").state == MailState.OFFLOADED
    again = auth_client.post(url, json={"account_id": box.acc.id})
    assert again.json() == {
        "offloaded": False,
        "reason": "It's no longer in Gmail, so nothing was changed.",
    }
    r = auth_client.post("/api/mail/messages/999999/offload", json={"account_id": box.acc.id})
    assert r.status_code == 404


async def test_offload_on_one_mail_says_when_google_makes_it_wait(auth_client, offload_box, app):
    box = offload_box
    await box.backup()

    async def wait(account_id, message_id):
        raise BudgetReached(1000)

    app.state.rule_applier.offload_by_hand = wait
    r = auth_client.post(
        f"/api/mail/messages/{box.row('Yesterday').id}/offload", json={"account_id": box.acc.id}
    )
    assert r.status_code == 503 and r.json()["detail"].endswith(
        "Nothing was offloaded; try again later."
    )


def test_the_old_delete_endpoint_is_gone(auth_client, make_account):
    acc = make_account()
    r = auth_client.post("/api/mail/delete", json={"account_id": acc.id, "message_ids": [1]})
    assert r.status_code in (404, 405)


def _offloaded_by_a_rule(box, *subjects):
    with box.app.state.session_factory() as db:
        for s in subjects:
            row = db.scalar(select(MailMessage).where(MailMessage.subject == s))
            row.state, row.offload_id = MailState.OFFLOADED, None
        db.commit()


async def test_mail_a_rule_offloaded_restores_untrashed_or_imported(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    in_trash, gone = box.row("Old statement"), box.row("Old photos")
    _offloaded_by_a_rule(box, "Old statement", "Old photos")
    box.fake.remove(gone.provider_id)  # Gmail emptied its trash
    r = _restore(auth_client, box, message_ids=[in_trash.id, gone.id])
    assert r.status_code == 202 and r.json()["count"] == 2
    await box.run_queued()
    assert box.row("Old statement").state == MailState.IN_MAIL
    assert box.row("Old statement").provider_id == in_trash.provider_id
    assert "TRASH" not in box.fake.messages[in_trash.provider_id].labels
    back = box.row("Old photos")
    assert back.state == MailState.IN_MAIL and back.provider_id != gone.provider_id
    assert box.fake.messages[back.provider_id].raw


async def test_mail_a_rule_offloaded_restores_by_search(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    _offloaded_by_a_rule(box, "Old statement")
    r = _restore(auth_client, box, q="statement")
    assert r.status_code == 202 and r.json()["count"] == 1
    await box.run_queued()
    assert box.row("Old statement").state == MailState.IN_MAIL


async def test_a_lost_import_answer_for_mail_a_rule_offloaded_never_makes_a_second_copy(
    auth_client, offload_box
):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    _offloaded_by_a_rule(box, "Old photos")
    box.fake.remove(row.provider_id)
    _lose_first_import_answer(box)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    await box.backup()  # the check sees the imported mail
    (copy,) = _copies(box, "Old photos")
    (after,) = [r for r in box.rows_all() if r.subject == "Old photos"]
    assert after.id == row.id and after.provider_id == copy.id
    assert after.state == MailState.IN_MAIL and after.import_started_at is None


@pytest.fixture
def mailbox(app, make_account, db, layout):
    acc = make_account()
    ensure_services(db, acc)
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    fake = FakeGmail(acc.email)
    app.state.mail.adapters = OneAdapter(fake_adapter(fake))

    def add(subject):
        raw = make_mail(subject)
        return keep(db, layout, acc, raw, pid=fake.add(raw))

    return acc, fake, add


def test_thread_list_carries_the_rule_chip(auth_client, mailbox, db):
    acc, _, add = mailbox
    row = add("Digest")
    db.add(
        RuleDecision(
            account_id=acc.id,
            message_id=row.id,
            rule_name="Newsletters",
            how="ai",
            why="a digest",
            status="suggested",
        )
    )
    db.commit()
    t = auth_client.get("/api/mail/threads", params={"account_id": acc.id}).json()["threads"][0]
    assert t["decision"]["rule_name"] == "Newsletters" and t["decision"]["status"] == "suggested"
    m = auth_client.get(f"/api/mail/messages/{row.id}").json()
    assert m["decision"]["why"] == "a digest"
    # The chip's Undo and "deleted forever" label depend on these two reaching the browser.
    assert m["decision"]["undoable"] is True and m["decision"]["forever"] is False


async def _offload_for_good_by_a_rule(box, app, subject):
    """A rule's Offload on a mailbox that allows permanent delete: the mail leaves Gmail."""
    from app.models import Account
    from app.services.accounts.oauth import FULL_MAIL_SCOPE

    await box.backup()
    row = box.row(subject)
    with app.state.session_factory() as db:
        acc = db.get(Account, box.acc.id)
        acc.scopes, acc.permanent_delete = FULL_MAIL_SCOPE, True
        d = RuleDecision(
            account_id=box.acc.id,
            message_id=row.id,
            rule_name="Junk",
            how=DecisionHow.AI,
            actions=[{"kind": "offload"}],
            labels_seen=list(row.labels),
            status=DecisionStatus.SUGGESTED,
        )
        db.add(d)
        db.commit()
        did = d.id
    assert await app.state.rule_applier.apply(did) == "applied"
    assert row.provider_id not in box.fake.messages
    return row


async def test_restoring_mail_a_rule_removed_for_good_marks_it_moved_back(
    auth_client, offload_box, app
):
    from app.services.rules.feedback import moved_back

    box = offload_box
    row = await _offload_for_good_by_a_rule(box, app, "Old photos")
    assert _restore(auth_client, box, message_ids=[row.id]).status_code == 202
    await box.run_queued()
    with app.state.session_factory() as db:
        assert moved_back(db, row.id)


async def test_mail_a_rule_removed_for_good_comes_back_in_the_inbox(auth_client, offload_box, app):
    box = offload_box
    row = await _offload_for_good_by_a_rule(box, app, "Old photos")
    assert _restore(auth_client, box, message_ids=[row.id]).status_code == 202
    await box.run_queued()
    back = box.row("Old photos")
    assert back.state == MailState.IN_MAIL and "INBOX" in back.labels
    assert "INBOX" in box.fake.messages[back.provider_id].labels


async def test_mail_a_rule_trashed_comes_back_in_the_inbox_after_gmail_purges_it(
    auth_client, offload_box, app
):
    box = offload_box
    await box.backup()
    row = box.row("Old photos")
    with app.state.session_factory() as db:
        d = RuleDecision(
            account_id=box.acc.id,
            message_id=row.id,
            rule_name="Junk",
            how=DecisionHow.AI,
            actions=[{"kind": "offload"}],
            labels_seen=list(row.labels),
            status=DecisionStatus.SUGGESTED,
        )
        db.add(d)
        db.commit()
        did = d.id
    assert await app.state.rule_applier.apply(did) == "applied"
    assert "TRASH" in box.fake.messages[row.provider_id].labels
    box.fake.remove(row.provider_id)  # Gmail emptied its trash
    assert _restore(auth_client, box, message_ids=[row.id]).status_code == 202
    await box.run_queued()
    back = box.row("Old photos")
    assert back.state == MailState.IN_MAIL and "INBOX" in back.labels
    assert "INBOX" in box.fake.messages[back.provider_id].labels
