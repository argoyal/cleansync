# backend/tests/test_rules_forever.py
"""Delete forever as a rule action (bulk offload spec §6)."""

import pytest
import yaml
from sqlalchemy import select

from app.devtools.fake_gmail import make_mail
from app.models import Account, Event, MailMessage, Rule, RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, RuleJobState
from app.services.accounts.oauth import FULL_MAIL_SCOPE
from app.services.accounts.permanent import NOT_ALLOWED_MSG, turn_off
from app.services.mail.adapter import MailUnreachable
from app.services.mail.store import eml_path
from app.services.rules.apply import UNREACHABLE_WAIT
from app.services.rules.decisions import DecisionProblem, Decisions
from app.services.rules.jobs import RuleJobProblem, RunScope
from app.services.rules.store import (
    RuleIn,
    RuleProblem,
    copy_rules,
    create_rule,
    import_yaml,
    update_rule,
)
from tests.rules_helpers import keep
from tests.test_rules_apply import decision, get
from tests.test_rules_apply_permanent import allow
from tests.test_rules_running import decided, jobs_for, rule

FOREVER = [{"kind": "delete_forever"}]


def _allowed(db, make_account):
    acc = make_account()
    acc.scopes, acc.permanent_delete = FULL_MAIL_SCOPE, True
    db.commit()
    return acc


def _rule(**kw):
    return RuleIn(name="Junk", description="junk mail", actions=FOREVER, **kw)


def test_it_cant_be_saved_without_permanent_delete(db, make_account):
    acc = make_account()
    with pytest.raises(
        RuleProblem, match=r"^Turn on Allow permanent delete in Settings → Accounts\.$"
    ):
        create_rule(db, acc.id, _rule(), "admin")


def test_it_always_suggests_first_and_stands_alone(db, make_account):
    acc = _allowed(db, make_account)
    with pytest.raises(RuleProblem, match="always suggests first"):
        create_rule(db, acc.id, _rule(mode="automatic"), "admin")
    both = RuleIn(name="x", description="d", actions=[*FOREVER, {"kind": "star"}])
    with pytest.raises(
        RuleProblem, match=r"^Delete forever can't be combined with other actions\.$"
    ):
        create_rule(db, acc.id, both, "admin")
    assert create_rule(db, acc.id, _rule(), "admin").actions == FOREVER


def test_a_rule_that_has_it_can_still_be_edited_once_the_permission_is_off(db, make_account):
    acc = _allowed(db, make_account)
    rule = create_rule(db, acc.id, _rule(), "admin")
    turn_off(db, acc.id)
    changed = update_rule(db, acc.id, rule.id, _rule(enabled=False), "admin")
    assert changed.enabled is False and changed.actions == FOREVER


async def test_on_approval_gmail_goes_first_then_the_copy_with_one_record_per_rule(gbox):
    allow(gbox)
    rows = [gbox.add("Junk 1"), gbox.add("Junk 2")]
    for row in rows:
        did = decision(gbox, row, FOREVER)
        assert await gbox.applier.apply(did) == DecisionStatus.APPLIED
        assert row.provider_id not in gbox.fake.messages
        assert get(gbox, MailMessage, row.id) is None
        assert get(gbox, RuleDecision, did) is None  # its rule history goes with it
        assert not eml_path(gbox.layout, gbox.acc.email, row.file_path).exists()
    with gbox.sf() as db:
        events = db.scalars(select(Event).where(Event.kind == "rule_deleted_forever")).all()
        assert [e.message for e in events] == ["Rule R deleted 2 mails forever"]
        assert [m["subject"] for m in events[0].data["mails"]] == ["Junk 1", "Junk 2"]


async def test_it_waits_with_its_reason_when_permanent_delete_is_off(gbox):
    row = gbox.add("Junk")
    did = decision(gbox, row, FOREVER)
    assert await gbox.applier.apply(did) == DecisionStatus.SUGGESTED
    assert get(gbox, RuleDecision, did).reason == NOT_ALLOWED_MSG
    assert row.provider_id in gbox.fake.messages and get(gbox, MailMessage, row.id) is not None
    planned = decision(gbox, gbox.add("Junk 2"), FOREVER, status=DecisionStatus.PLANNED)
    assert await gbox.applier.apply(planned) == DecisionStatus.FAILED  # a run moves on
    assert get(gbox, RuleDecision, planned).reason == NOT_ALLOWED_MSG


async def test_google_refusing_it_turns_the_switch_off_and_keeps_the_mail(gbox):
    allow(gbox)
    gbox.fake.fail("DELETE", r"/messages/", 403, reason="insufficientPermissions")
    row = gbox.add("Junk")
    did = decision(gbox, row, FOREVER)
    assert await gbox.applier.apply(did) == DecisionStatus.SUGGESTED
    assert get(gbox, RuleDecision, did).reason == NOT_ALLOWED_MSG
    assert get(gbox, MailMessage, row.id) is not None and row.provider_id in gbox.fake.messages
    assert get(gbox, Account, gbox.acc.id).permanent_delete is False


async def test_a_mail_gmail_no_longer_has_still_loses_its_copy(gbox):
    allow(gbox)
    row = gbox.add("Junk")
    gbox.fake.remove(row.provider_id)
    did = decision(gbox, row, FOREVER)
    assert await gbox.applier.apply(did) == DecisionStatus.APPLIED
    assert get(gbox, MailMessage, row.id) is None


async def test_approving_it_in_suggested_needs_the_typed_count(gbox):
    allow(gbox)
    rows = [gbox.add("Junk 1"), gbox.add("Junk 2")]
    ids = [decision(gbox, r, FOREVER) for r in rows]
    decisions = Decisions(gbox.sf, gbox.applier)
    with pytest.raises(DecisionProblem, match=r"^Type 2 to delete these mails forever\.$"):
        await decisions.approve(gbox.acc.id, ids)
    with pytest.raises(DecisionProblem):
        await decisions.approve(gbox.acc.id, ids, confirm=1)
    assert all(r.provider_id in gbox.fake.messages for r in rows)
    assert await decisions.approve(gbox.acc.id, ids, confirm=2) == {"applied": 2}


async def test_a_run_deletes_forever_after_the_typed_count_and_keeps_going(gbox):
    allow(gbox)
    junk = rule(gbox, "Junk", actions=FOREVER)
    rows = [gbox.add("Junk 1"), gbox.add("Junk 2")]
    worker, jobs = jobs_for(gbox)
    jid = await decided(gbox, worker, jobs, [junk], RunScope(inbox=True))
    with pytest.raises(RuleJobProblem, match=r"doesn't match the 2 mails to delete forever\."):
        jobs.run(gbox.acc.id, jid, 1)
    assert all(r.provider_id in gbox.fake.messages for r in rows)  # nothing happened yet
    jobs.run(gbox.acc.id, jid, 2)
    await jobs.wait(jid)
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.state, p.applied, p.failed) == (RuleJobState.DONE, 2, 0)
    for r in rows:
        assert r.provider_id not in gbox.fake.messages
        assert get(gbox, MailMessage, r.id) is None
        assert not eml_path(gbox.layout, gbox.acc.email, r.file_path).exists()
    with gbox.sf() as db:
        (event,) = db.scalars(select(Event).where(Event.kind == "rule_deleted_forever")).all()
        assert event.message == "Rule Junk deleted 2 mails forever"


def test_the_rule_card_says_why_a_delete_forever_rule_waits(auth_client, db, make_account):
    acc = _allowed(db, make_account)
    create_rule(db, acc.id, _rule(), "admin")
    rules = auth_client.get("/api/rules", params={"account_id": acc.id}).json()
    assert rules[0]["blocked"] is None
    turn_off(db, acc.id)
    rules = auth_client.get("/api/rules", params={"account_id": acc.id}).json()
    assert rules[0]["blocked"] == NOT_ALLOWED_MSG


def test_the_approve_endpoint_checks_the_typed_count_on_the_server(
    auth_client, db, layout, make_account
):
    acc = _allowed(db, make_account)
    row = keep(db, layout, acc, make_mail("Junk"))
    d = RuleDecision(
        account_id=acc.id,
        message_id=row.id,
        how=DecisionHow.AI,
        actions=FOREVER,
        status=DecisionStatus.SUGGESTED,
        rule_name="R",
    )
    db.add(d)
    db.commit()
    body = {"account_id": acc.id, "ids": [d.id]}
    refused = auth_client.post("/api/rule-decisions/approve", json=body)
    assert refused.status_code == 400
    assert refused.json()["detail"] == "Type 1 to delete these mails forever."
    wrong = auth_client.post("/api/rule-decisions/approve", json={**body, "confirm": 5})
    assert wrong.status_code == 400
    db.expire_all()
    assert db.get(MailMessage, row.id) is not None  # nothing was deleted


async def test_correcting_to_a_delete_forever_rule_only_suggests_it(gbox):
    allow(gbox)
    target = rule(gbox, "Junk", actions=FOREVER)
    row = gbox.add("Junk")
    first = decision(gbox, row, [{"kind": "archive"}])
    decisions = Decisions(gbox.sf, gbox.applier)
    new_id = await decisions.correct(gbox.acc.id, first, target)
    assert row.provider_id in gbox.fake.messages and get(gbox, MailMessage, row.id) is not None
    assert eml_path(gbox.layout, gbox.acc.email, row.file_path).exists()
    assert get(gbox, RuleDecision, new_id).status == DecisionStatus.SUGGESTED
    with pytest.raises(DecisionProblem):
        await decisions.approve(gbox.acc.id, [new_id])
    assert await decisions.approve(gbox.acc.id, [new_id], confirm=1) == {"applied": 1}
    assert row.provider_id not in gbox.fake.messages and get(gbox, MailMessage, row.id) is None


def test_a_yaml_import_with_a_delete_forever_rule_saves_nothing_without_the_permission(
    db, make_account
):
    acc = make_account()
    text = yaml.safe_dump(
        {
            "rules": [
                {"name": "A", "description": "a", "actions": [{"kind": "archive"}]},
                {"name": "B", "description": "b", "actions": [{"kind": "star"}]},
                {"name": "C", "description": "c", "actions": FOREVER},
            ]
        }
    )
    with pytest.raises(RuleProblem, match=r"^Turn on Allow permanent delete"):
        import_yaml(db, acc.id, text, "admin")
    assert db.scalars(select(Rule).where(Rule.account_id == acc.id)).all() == []


def test_copying_rules_to_a_mailbox_without_the_permission_copies_none(db, make_account):
    src, dst = _allowed(db, make_account), make_account("bob@example.com")
    create_rule(db, src.id, RuleIn(name="A", description="a", actions=[{"kind": "star"}]), "a")
    create_rule(db, src.id, _rule(), "a")
    with pytest.raises(RuleProblem, match=r"^Turn on Allow permanent delete"):
        copy_rules(db, src.id, dst.id, "admin")
    assert db.scalars(select(Rule).where(Rule.account_id == dst.id)).all() == []


def test_updating_a_rule_to_add_delete_forever_needs_the_permission_and_suggest_first(
    db, make_account
):
    acc = make_account()
    rule_ = create_rule(
        db, acc.id, RuleIn(name="A", description="a", actions=[{"kind": "star"}]), "a"
    )
    with pytest.raises(RuleProblem, match=r"^Turn on Allow permanent delete"):
        update_rule(db, acc.id, rule_.id, _rule(), "admin")
    acc.scopes, acc.permanent_delete = FULL_MAIL_SCOPE, True
    db.commit()
    with pytest.raises(RuleProblem, match="always suggests first"):
        update_rule(db, acc.id, rule_.id, _rule(mode="automatic"), "admin")


async def test_gmail_unreachable_keeps_the_copy_and_the_decision_waiting(gbox):
    allow(gbox)
    row = gbox.add("Junk")
    did = decision(gbox, row, FOREVER)

    async def down(pid):
        raise MailUnreachable("down")

    gbox.adapter.delete = down
    with pytest.raises(MailUnreachable):
        await gbox.applier.apply(did)
    assert get(gbox, MailMessage, row.id) is not None
    d = get(gbox, RuleDecision, did)
    assert d.status == DecisionStatus.SUGGESTED and d.reason == UNREACHABLE_WAIT
