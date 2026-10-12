import pytest

from app.devtools.fake_gmail import FakeGmail, make_mail
from app.models import RuleDecision
from app.models.enums import ServiceKind, ServiceState
from app.services.accounts.services import ensure_services, set_service_state
from app.services.google.quota import BudgetReached
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


def test_delete_moves_to_archive_and_hides_from_threads(auth_client, mailbox):
    acc, fake, add = mailbox
    row = add("Promo")
    r = auth_client.post("/api/mail/delete", json={"account_id": acc.id, "message_ids": [row.id]})
    assert r.json() == {"deleted": 1, "failed": []}
    assert "TRASH" in fake.messages[row.provider_id].labels
    main = auth_client.get("/api/mail/threads", params={"account_id": acc.id}).json()["threads"]
    arch = auth_client.get(
        "/api/mail/threads", params={"account_id": acc.id, "view": "archive"}
    ).json()["threads"]
    assert main == [] and [t["subject"] for t in arch] == ["Promo"]
    assert (
        auth_client.get(f"/api/mail/threads/{row.thread_id}", params={"account_id": acc.id}).json()
        == []
    )
    one = auth_client.get(
        f"/api/mail/threads/{row.thread_id}", params={"account_id": acc.id, "archive": True}
    ).json()
    assert one[0]["state"] == "local_archive"


def test_delete_by_thread_and_unknown_mail(auth_client, mailbox):
    acc, _, add = mailbox
    row = add("Promo")
    r = auth_client.post(
        "/api/mail/delete",
        json={"account_id": acc.id, "thread_ids": [row.thread_id], "message_ids": [999999]},
    )
    body = r.json()
    assert body["deleted"] == 1 and [f["message_id"] for f in body["failed"]] == [999999]
    assert auth_client.post("/api/mail/delete", json={"account_id": acc.id}).status_code == 400


def test_delete_stops_politely_when_google_has_to_wait(auth_client, mailbox, app):
    acc, _, add = mailbox
    row = add("Promo")

    async def wait(account_id, message_id):
        raise BudgetReached(1000)

    app.state.rule_applier.delete_by_hand = wait
    r = auth_client.post("/api/mail/delete", json={"account_id": acc.id, "message_ids": [row.id]})
    assert r.status_code == 503 and "0 deleted so far" in r.json()["detail"]


def test_delete_forever_needs_the_count(auth_client, mailbox):
    acc, _, add = mailbox
    row = add("Promo")
    auth_client.post("/api/mail/delete", json={"account_id": acc.id, "message_ids": [row.id]})
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
    auth_client.post("/api/mail/delete", json={"account_id": acc.id, "message_ids": [row.id]})
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
