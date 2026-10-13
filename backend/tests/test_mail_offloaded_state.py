"""Mail panel spec §8: one "removed from Gmail" state. A rule's Offload and Offload on one
mail reach it through the applier; the one restore path leaves it."""

from app.models import RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, MailState
from app.services.google.quota import BudgetReached
from tests.test_mail_restore import _restore


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
