import pytest
from sqlalchemy import select

from app.devtools.fake_gmail import make_mail
from app.models import RuleDecision, RuleVersion
from app.models.enums import DecisionHow, DecisionStatus, RuleCombine
from app.services.rules.store import (
    RuleIn,
    RuleNotFound,
    RuleProblem,
    add_starters,
    copy_rules,
    create_rule,
    delete_rule,
    export_yaml,
    get_rule,
    import_yaml,
    reorder,
    rule_views,
    update_rule,
)
from tests.rules_helpers import keep

NEWS = RuleIn(
    name="Newsletters",
    description="Digests I signed up for",
    actions=[{"kind": "label", "label": " Newsletters "}, {"kind": "archive"}],
)


def test_create_cleans_and_versions(db, make_account):
    acc = make_account()
    r = create_rule(db, acc.id, NEWS, "admin")
    assert r.actions == [{"kind": "label", "label": "Newsletters"}, {"kind": "archive"}]
    assert r.version == 1 and r.position == 0 and r.mode == "suggest"
    assert (
        db.scalar(select(RuleVersion).where(RuleVersion.rule_id == r.id)).snapshot["name"]
        == "Newsletters"
    )


@pytest.mark.parametrize(
    "data,msg",
    [
        (RuleIn(name="", description="d", actions=[{"kind": "archive"}]), "name"),
        (RuleIn(name="x", description="d", actions=[]), "at least one action"),
        (RuleIn(name="x", description="d", actions=[{"kind": "send"}]), "isn't an action"),
        (RuleIn(name="x", description="d", actions=[{"kind": "label", "label": " "}]), "label"),
        (
            RuleIn(name="x", description="d", actions=[{"kind": "delete"}, {"kind": "star"}]),
            "Delete",
        ),
        (RuleIn(name="x", description="", actions=[{"kind": "archive"}]), "description"),
        (
            RuleIn(name="x", combine=RuleCombine.FIXED_ONLY, actions=[{"kind": "archive"}]),
            "fixed condition",
        ),
    ],
)
def test_bad_rules_are_refused(db, make_account, data, msg):
    with pytest.raises(RuleProblem, match=msg):
        create_rule(db, make_account().id, data, "admin")


def test_edit_makes_a_new_version_only_when_something_changed(db, make_account):
    acc = make_account()
    r = create_rule(db, acc.id, NEWS, "admin")
    update_rule(db, acc.id, r.id, NEWS, "admin")
    assert r.version == 1
    update_rule(db, acc.id, r.id, RuleIn(**{**NEWS.__dict__, "description": "Other"}), "admin")
    assert r.version == 2


def test_rules_belong_to_one_mailbox(db, make_account):
    a, b = make_account(), make_account("bob@example.com")
    r = create_rule(db, a.id, NEWS, "admin")
    with pytest.raises(RuleNotFound):
        get_rule(db, b.id, r.id)
    assert rule_views(db, b.id) == []


def test_delete_withdraws_waiting_decisions(db, layout, make_account):
    acc = make_account()
    r = create_rule(db, acc.id, NEWS, "admin")
    mail = keep(db, layout, acc, make_mail("Digest"))
    db.add(
        RuleDecision(
            account_id=acc.id,
            message_id=mail.id,
            rule_id=r.id,
            how=DecisionHow.AI,
            status=DecisionStatus.SUGGESTED,
        )
    )
    db.commit()
    delete_rule(db, acc.id, r.id)
    d = db.scalar(select(RuleDecision))
    assert d.status == DecisionStatus.WITHDRAWN and "deleted" in d.reason


def test_reorder_and_views_in_order(db, make_account):
    acc = make_account()
    one = create_rule(db, acc.id, NEWS, "admin")
    two = create_rule(
        db,
        acc.id,
        RuleIn(name="Receipts", description="Receipts", actions=[{"kind": "star"}]),
        "admin",
    )
    reorder(db, acc.id, [two.id, one.id])
    assert [v.name for v in rule_views(db, acc.id)] == ["Receipts", "Newsletters"]
    with pytest.raises(RuleProblem):
        reorder(db, acc.id, [two.id])


def test_starters_are_added_once_in_suggest_mode(db, make_account):
    acc = make_account()
    add_starters(db, acc.id, ["Newsletters", "Calendar"], "admin")
    add_starters(db, acc.id, ["Newsletters"], "admin")
    views = rule_views(db, acc.id)
    assert [v.name for v in views] == ["Newsletters", "Calendar"]
    assert all(v.mode == "suggest" for v in views)


def test_yaml_round_trip_and_copy(db, make_account):
    a, b = make_account(), make_account("bob@example.com")
    create_rule(db, a.id, NEWS, "admin")
    text = export_yaml(db, a.id)
    assert "Newsletters" in text and "account" not in text
    import_yaml(db, b.id, text, "admin")
    import_yaml(db, b.id, text, "admin")
    assert [v.name for v in rule_views(db, b.id)] == ["Newsletters", "Newsletters (2)"]
    assert copy_rules(db, a.id, make_account("carol@example.com").id, "admin") == 1
    with pytest.raises(RuleProblem):
        import_yaml(db, b.id, "rules: [: bad", "admin")


def test_yaml_rule_without_a_name_is_refused(db, make_account):
    acc = make_account()
    with pytest.raises(RuleProblem, match="name"):
        import_yaml(
            db, acc.id, "rules:\n  - description: d\n    actions: [{kind: archive}]\n", "admin"
        )
