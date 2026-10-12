# backend/tests/test_rules_api.py
import pytest
from sqlalchemy import select

from app.models import RuleDecision, RuleTodo
from app.models.enums import ServiceKind, ServiceState
from app.services.accounts.services import ensure_services, set_service_state


@pytest.fixture
def acc(make_account, db):
    a = make_account()
    ensure_services(db, a)
    set_service_state(db, a.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    return a


RULE = {"name": "Newsletters", "description": "Digests", "actions": [{"kind": "archive"}]}


def test_rule_crud_is_per_mailbox(auth_client, acc, make_account):
    r = auth_client.post("/api/rules", json={"account_id": acc.id, **RULE})
    assert r.status_code == 201 and r.json()["mode"] == "suggest"
    rid = r.json()["id"]
    other = make_account("bob@example.com")
    assert auth_client.get("/api/rules", params={"account_id": other.id}).json() == []
    assert (
        auth_client.put(f"/api/rules/{rid}", json={"account_id": other.id, **RULE}).status_code
        == 404
    )
    bad = auth_client.post("/api/rules", json={"account_id": acc.id, **RULE, "actions": []})
    assert bad.status_code == 400 and "action" in bad.json()["detail"]
    assert auth_client.delete(f"/api/rules/{rid}", params={"account_id": acc.id}).status_code == 204


def test_export_import_round_trip(auth_client, acc):
    auth_client.post("/api/rules", json={"account_id": acc.id, **RULE})
    y = auth_client.get("/api/rules/export", params={"account_id": acc.id})
    assert (
        y.headers["content-type"].startswith("text/yaml")
        and "attachment" in y.headers["content-disposition"]
    )
    out = auth_client.post("/api/rules/import", json={"account_id": acc.id, "yaml": y.text})
    assert [r["name"] for r in out.json()] == ["Newsletters (2)"]


def test_draft_without_a_model_still_answers(auth_client, acc):
    d = auth_client.post(
        "/api/rules/draft", json={"account_id": acc.id, "sentence": "Shipping updates"}
    ).json()
    assert d["description"] == "Shipping updates" and "No model" in d["notes"][0]


def test_sorting_switch_and_status(auth_client, acc):
    s = auth_client.get("/api/rules/sorting", params={"account_id": acc.id}).json()
    assert s == {
        "enabled": False,
        "waiting": 0,
        "sorted_today": 0,
        "suggested": 0,
        "failing_since": None,
        "last_error": None,
        "warning": False,
        "model_set": False,
    }
    s = auth_client.put("/api/rules/sorting", json={"account_id": acc.id, "enabled": True}).json()
    assert s["enabled"] is True


def test_decisions_list_and_approve(auth_client, acc, app, layout, db):
    from app.devtools.fake_gmail import FakeGmail, make_mail
    from tests.mail_helpers import fake_adapter
    from tests.rules_helpers import keep
    from tests.test_mail_api import OneAdapter

    fake = FakeGmail(acc.email)
    raw = make_mail("Digest")
    pid = fake.add(raw)
    row = keep(db, layout, acc, raw, pid=pid)
    app.state.mail.adapters = OneAdapter(fake_adapter(fake))
    rid = auth_client.post("/api/rules", json={"account_id": acc.id, **RULE}).json()["id"]
    db.add(
        RuleDecision(
            account_id=acc.id,
            message_id=row.id,
            rule_id=rid,
            rule_version=1,
            rule_name="Newsletters",
            how="ai",
            actions=RULE["actions"],
            labels_seen=["INBOX"],
            status="suggested",
        )
    )
    db.commit()
    listed = auth_client.get(
        "/api/rule-decisions", params={"account_id": acc.id, "status": "suggested"}
    ).json()
    assert listed[0]["subject"] == "Digest"
    out = auth_client.post(
        "/api/rule-decisions/approve", json={"account_id": acc.id, "ids": [listed[0]["id"]]}
    )
    assert out.json() == {"applied": 1}
    assert "INBOX" not in fake.messages[pid].labels


def test_rule_job_start_and_refusals(auth_client, acc, db, layout):
    from app.devtools.fake_gmail import make_mail
    from tests.rules_helpers import keep

    r = auth_client.post("/api/rule-jobs", json={"account_id": acc.id, "q": "", "view": "inbox"})
    assert r.status_code == 400 and "No mail" in r.json()["detail"]
    keep(db, layout, acc, make_mail("Old one"))
    r = auth_client.post("/api/rule-jobs", json={"account_id": acc.id, "q": "", "view": "inbox"})
    assert r.status_code == 202
    jid = r.json()["job_id"]
    assert db.scalars(select(RuleTodo)).all()
    p = auth_client.get(f"/api/rule-jobs/{jid}", params={"account_id": acc.id}).json()
    assert p["state"] == "PREVIEWING" and p["total"] == 1
    r = auth_client.post(f"/api/rule-jobs/{jid}/run", json={"account_id": acc.id, "confirm": 1})
    assert r.status_code == 400 and "preview" in r.json()["detail"]


def test_correct_waits_become_503_and_changes_are_published(auth_client, acc, app, monkeypatch):
    from app.services.rules.apply import BudgetReached

    seen = []
    monkeypatch.setattr(app.state.bus, "publish", lambda kind, data=None: seen.append((kind, data)))

    async def waiting(*a, **k):
        raise BudgetReached(1000)

    monkeypatch.setattr(app.state.rule_decisions, "correct", waiting)
    r = auth_client.post(
        "/api/rule-decisions/1/correct", json={"account_id": acc.id, "rule_id": None}
    )
    assert r.status_code == 503 and "Try again later" in r.json()["detail"]
    assert "BudgetReached" not in r.text and not seen
    r = auth_client.post("/api/rule-decisions/reject", json={"account_id": acc.id, "ids": [1]})
    assert r.json() == {"rejected": 0} and ("rules", {"account_id": acc.id}) in seen
