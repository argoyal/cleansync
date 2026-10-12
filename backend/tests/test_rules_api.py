import time
from datetime import timedelta

# backend/tests/test_rules_api.py
import pytest
from sqlalchemy import delete, select

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


def test_a_previewed_job_runs_and_stops_from_the_api(auth_client, acc, db, layout):
    """The run starts a task on the server's event loop (it once failed with "no current event
    loop" because the endpoint ran in a worker thread)."""
    from app.devtools.fake_gmail import make_mail
    from app.models import RuleJob
    from app.models.enums import RuleJobState
    from tests.rules_helpers import keep

    keep(db, layout, acc, make_mail("Old one"))
    jid = auth_client.post(
        "/api/rule-jobs", json={"account_id": acc.id, "q": "", "view": "inbox"}
    ).json()["job_id"]
    db.execute(delete(RuleTodo))
    db.get(RuleJob, jid).state = RuleJobState.PREVIEWED  # nothing planned: confirm 0
    db.commit()
    r = auth_client.post(f"/api/rule-jobs/{jid}/run", json={"account_id": acc.id, "confirm": 0})
    assert r.status_code == 202, r.text
    for _ in range(50):  # the run is a task on the app's loop: give it a moment
        p = auth_client.get(f"/api/rule-jobs/{jid}", params={"account_id": acc.id}).json()
        if p["state"] == "DONE":
            break
        time.sleep(0.05)
    assert p["state"] == "DONE"
    r = auth_client.post(f"/api/rule-jobs/{jid}/stop", json={"account_id": acc.id})
    assert r.status_code == 204, r.text


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


@pytest.mark.parametrize(
    ("ago", "warn"), [(timedelta(hours=2), True), (timedelta(minutes=30), False)]
)
def test_sorting_warning_after_an_hour_of_failing(auth_client, acc, db, ago, warn):
    from app.core.clock import utcnow
    from app.services.rules.worker import set_sorting

    ra = set_sorting(db, acc.id, True)
    ra.failing_since = utcnow() - ago
    ra.last_error = "Model unreachable"
    db.commit()
    db.expire_all()
    r = auth_client.get("/api/rules/sorting", params={"account_id": acc.id})
    assert r.status_code == 200 and r.json()["warning"] is warn
    assert r.json()["failing_since"] is not None


def test_try_it_without_a_model_uses_fixed_conditions_only(auth_client, acc, db, layout):
    from app.devtools.fake_gmail import make_mail
    from tests.rules_helpers import keep

    keep(db, layout, acc, make_mail("Weekly digest", sender="news@example.com"))
    keep(db, layout, acc, make_mail("Lunch?", sender="pal@example.com"))
    rule = {
        "name": "News",
        "conditions": {"senders_in": ["news@example.com"]},
        "combine": "fixed_only",
        "actions": [{"kind": "archive"}],
    }
    out = auth_client.post("/api/rules/try", json={"account_id": acc.id, "rule": rule}).json()
    by_subject = {r["subject"]: r for r in out}
    assert by_subject["Weekly digest"]["this_rule"] is True
    assert by_subject["Lunch?"]["this_rule"] is False


def test_undo_through_the_api(auth_client, acc, app, layout, db):
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
    did = db.scalar(select(RuleDecision.id))
    auth_client.post("/api/rule-decisions/approve", json={"account_id": acc.id, "ids": [did]})
    assert "INBOX" not in fake.messages[pid].labels
    out = auth_client.post("/api/rule-decisions/undo", json={"account_id": acc.id, "ids": [did]})
    assert out.status_code == 200 and out.json()[0]["status"] == "undone"
    assert "INBOX" in fake.messages[pid].labels


def test_another_mailbox_cannot_touch_this_ones_objects(auth_client, acc, make_account, db, layout):
    from app.devtools.fake_gmail import make_mail
    from app.models import Rule, RuleJob, RuleSender
    from tests.rules_helpers import keep

    b = make_account("bob@example.com")
    row = keep(db, layout, acc, make_mail("Private"))
    rid = auth_client.post("/api/rules", json={"account_id": acc.id, **RULE}).json()["id"]
    other_rule = auth_client.post("/api/rules", json={"account_id": b.id, **RULE}).json()["id"]
    sender = RuleSender(rule_id=rid, account_id=acc.id, sender="a@example.com", approvals=3)
    dec = RuleDecision(
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
    db.add_all([sender, dec])
    db.commit()
    jid = auth_client.post(
        "/api/rule-jobs", json={"account_id": acc.id, "q": "", "view": "inbox"}
    ).json()["job_id"]
    sid, did = sender.id, dec.id
    B = {"account_id": b.id}

    assert (
        auth_client.put(f"/api/rules/{rid}", json={**B, **RULE, "name": "Hijacked"}).status_code
        == 404
    )
    assert auth_client.delete(f"/api/rules/{rid}", params=B).status_code == 404
    assert auth_client.delete(f"/api/rules/{rid}/senders/{sid}", params=B).status_code == 404
    assert auth_client.post("/api/rule-decisions/approve", json={**B, "ids": [did]}).json() in (
        {},
        {"applied": 0},
    )
    assert auth_client.post("/api/rule-decisions/reject", json={**B, "ids": [did]}).json() == {
        "rejected": 0
    }
    assert auth_client.post("/api/rule-decisions/undo", json={**B, "ids": [did]}).json() == []
    assert auth_client.post(
        f"/api/rule-decisions/{did}/correct", json={**B, "rule_id": None}
    ).status_code in (400, 404)
    assert auth_client.post(
        f"/api/rule-decisions/{did}/correct", json={**B, "rule_id": rid}
    ).status_code in (400, 404)
    assert auth_client.get(f"/api/rule-jobs/{jid}", params=B).status_code in (400, 404)
    assert auth_client.post(f"/api/rule-jobs/{jid}/run", json={**B, "confirm": 1}).status_code in (
        400,
        404,
    )
    assert auth_client.post(f"/api/rule-jobs/{jid}/stop", json=B).status_code in (400, 404)
    r = auth_client.post("/api/rules/draft-from-mail", json={**B, "message_ids": [row.id]})
    assert r.status_code == 400 and "None of those mails" in r.json()["detail"]
    assert (
        auth_client.post("/api/rules/try", json={**B, "rule": RULE, "message_ids": [row.id]}).json()
        == []
    )

    db.expire_all()
    assert (
        db.get(Rule, rid).name == "Newsletters" and db.get(Rule, other_rule).name == "Newsletters"
    )
    assert db.get(RuleSender, sid) is not None
    assert db.get(RuleDecision, did).status == "suggested"
    assert db.get(RuleJob, jid).state == "PREVIEWING"
    assert db.scalars(select(RuleDecision)).all() == [db.get(RuleDecision, did)]


def test_try_it_with_an_empty_search_looks_at_the_inbox(auth_client, acc, db, layout):
    from app.devtools.fake_gmail import make_mail
    from tests.rules_helpers import keep

    keep(db, layout, acc, make_mail("In the inbox"))
    keep(db, layout, acc, make_mail("Archived already"), labels=())
    rule = {**RULE, "combine": "fixed_only", "conditions": {"subject_contains": ["a"]}}
    out = auth_client.post("/api/rules/try", json={"account_id": acc.id, "rule": rule}).json()
    assert [r["subject"] for r in out] == ["In the inbox"]
    out = auth_client.post(
        "/api/rules/try", json={"account_id": acc.id, "rule": rule, "q": "archived"}
    ).json()
    assert [r["subject"] for r in out] == ["Archived already"]


def test_try_and_draft_read_mail_files_off_the_event_loop(
    auth_client, acc, db, layout, monkeypatch
):
    import asyncio

    from app.api import rules as rules_api
    from app.devtools.fake_gmail import make_mail
    from tests.rules_helpers import keep

    row = keep(db, layout, acc, make_mail("Hello"))
    real, on_loop = rules_api.mail_for_model, []

    def spy(*a, **kw):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*a, **kw)

    monkeypatch.setattr(rules_api, "mail_for_model", spy)
    B = {"account_id": acc.id, "message_ids": [row.id]}
    auth_client.post("/api/rules/try", json={**B, "rule": RULE})
    auth_client.post("/api/rules/draft-from-mail", json=B)
    assert on_loop == [False, False]


def test_the_open_run_of_rules_can_be_found_again(auth_client, acc, db, layout):
    from app.devtools.fake_gmail import make_mail
    from tests.rules_helpers import keep

    B = {"account_id": acc.id}
    r = auth_client.get("/api/rule-jobs/open", params=B)
    assert r.status_code == 200 and r.json() is None
    keep(db, layout, acc, make_mail("Old one"))
    jid = auth_client.post("/api/rule-jobs", json={**B, "q": "", "view": "inbox"}).json()["job_id"]
    p = auth_client.get("/api/rule-jobs/open", params=B).json()
    assert p["job_id"] == jid and p["state"] == "PREVIEWING" and p["total"] == 1
    auth_client.post(f"/api/rule-jobs/{jid}/stop", json=B)
    assert auth_client.get("/api/rule-jobs/open", params=B).json() is None
