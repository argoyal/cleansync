import pytest

from app.devtools.fake_gmail import FakeGmail, make_mail
from app.models import RuleDecision
from app.models.enums import ServiceKind, ServiceState
from app.services.accounts.services import ensure_services, set_service_state
from tests.mail_helpers import fake_adapter
from tests.rules_helpers import keep
from tests.test_mail_api import OneAdapter


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


def test_delete_forever_needs_the_count(auth_client, mailbox):
    acc, _, add = mailbox
    row = add("Promo")
    auth_client.post(f"/api/mail/messages/{row.id}/offload", json={"account_id": acc.id})
    r = auth_client.post(
        "/api/mail/archive/delete-forever",
        json={"account_id": acc.id, "message_ids": [row.id], "confirm": 2},
    )
    assert r.status_code == 400
    r = auth_client.post(
        "/api/mail/archive/delete-forever",
        json={"account_id": acc.id, "message_ids": [row.id], "confirm": 1},
    )
    assert r.json() == {"deleted": 1, "left": 0}


def test_delete_forever_refuses_while_a_restore_is_queued(auth_client, mailbox, app):
    acc, _, add = mailbox
    row = add("Promo")
    auth_client.post(f"/api/mail/messages/{row.id}/offload", json={"account_id": acc.id})
    app.state.mail.queue.waiting = lambda aid: [("restore", {})]
    r = auth_client.post(
        "/api/mail/archive/delete-forever",
        json={"account_id": acc.id, "message_ids": [row.id], "confirm": 1},
    )
    assert r.status_code == 400 and "A restore is running" in r.json()["detail"]


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


def test_delete_forever_with_an_empty_pick_never_falls_back_to_the_search(auth_client, mailbox):
    acc, _, add = mailbox
    row = add("Promo")
    auth_client.post(f"/api/mail/messages/{row.id}/offload", json={"account_id": acc.id})
    r = auth_client.post(
        "/api/mail/archive/delete-forever",
        json={"account_id": acc.id, "message_ids": [], "q": "Promo", "confirm": 1},
    )
    assert r.status_code == 400 and r.json()["detail"] == "Pick mails or type a search."
    one = auth_client.get(
        f"/api/mail/threads/{row.thread_id}", params={"account_id": acc.id, "view": "offloaded"}
    ).json()
    assert one[0]["state"] == "offloaded"
