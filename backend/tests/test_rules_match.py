from datetime import UTC, datetime

import pytest

from app.models.enums import DecisionHow, RuleCombine
from app.services.rules.mailview import MailForModel
from app.services.rules.match import Choice, Conditions, RuleView, decide, fixed_match


def mail(**kw) -> MailForModel:
    base = dict(
        message_id=1,
        sender="news@letters.example.com",
        sender_name="",
        to=["alice@example.com"],
        cc=[],
        owner_in="to",
        subject="Weekly digest",
        date=datetime(2026, 10, 1, tzinfo=UTC),
        attachments=[],
        list_unsubscribe=True,
        mailing_list=True,
        calendar_invite=False,
        text="hello",
        labels=["INBOX"],
    )
    return MailForModel(**(base | kw))


def rule(id, combine=RuleCombine.AI_ONLY, **cond) -> RuleView:
    return RuleView(
        id,
        1,
        f"R{id}",
        f"rule {id}",
        Conditions(**cond),
        combine,
        ({"kind": "archive"},),
        "suggest",
    )


class FakeChooser:
    def __init__(self, answer: int | None, raw=None):
        self.answer, self.raw, self.seen = answer, raw, None

    async def choose(self, rules, m, hints):
        self.seen = (rules, hints)
        return Choice(self.answer, "because", "fake", 5, self.raw)


@pytest.mark.parametrize(
    "cond,expected",
    [
        ({"senders_in": ("example.com",)}, True),  # a parent domain matches
        ({"senders_in": ("letters.example.com",)}, True),
        ({"senders_in": ("other.com",)}, False),
        ({"senders_in": ("news@letters.example.com",)}, True),
        ({"senders_not_in": ("example.com",)}, False),
        ({"subject_contains": ("DIGEST",)}, True),
        ({"list_unsubscribe": False}, False),
        ({"addressed": "direct"}, True),
        ({"addressed": "cc"}, False),
        ({"addressed": "list"}, True),
        ({"has_attachments": True}, False),
    ],
)
def test_fixed_conditions(cond, expected):
    assert fixed_match(Conditions(**cond), mail()) is expected


def test_conditions_round_trip_json():
    c = Conditions(senders_in=("a.com",), list_unsubscribe=True)
    assert Conditions.from_json(c.to_json()) == c
    assert Conditions.from_json({"senders_in": ["A.COM "], "junk": 1}).senders_in == ("a.com",)
    assert Conditions().is_empty() and Conditions.from_json(None).is_empty()


async def test_remembered_sender_wins_without_asking():
    ch = FakeChooser(1)
    v = await decide(
        [rule(1), rule(2)], mail(), remembered={"news@letters.example.com": 2}, chooser=ch, hints=[]
    )
    assert v.rule.id == 2 and v.how == DecisionHow.REMEMBERED and ch.seen is None


async def test_first_fixed_rule_in_order_wins_before_ai():
    rules = [
        rule(1),
        rule(2, RuleCombine.FIXED_ONLY, subject_contains=("digest",)),
        rule(3, RuleCombine.FIXED_DECIDES, list_unsubscribe=True),
    ]
    ch = FakeChooser(1)
    v = await decide(rules, mail(), remembered={}, chooser=ch, hints=[])
    assert v.rule.id == 2 and v.how == DecisionHow.FIXED and ch.seen is None


async def test_candidates_for_the_ai():
    rules = [
        rule(1),  # ai_only: a candidate
        rule(2, RuleCombine.FIXED_THEN_AI, subject_contains=("invoice",)),  # no hit: not
        rule(3, RuleCombine.FIXED_THEN_AI, list_unsubscribe=True),  # hit: a candidate
        rule(4, RuleCombine.FIXED_DECIDES, has_attachments=True),  # no hit: asks the AI
        rule(5, RuleCombine.FIXED_ONLY, has_attachments=True),  # no hit: never
    ]
    ch = FakeChooser(3)
    v = await decide(rules, mail(), remembered={}, chooser=ch, hints=["h"])
    assert [r.id for r in ch.seen[0]] == [1, 3, 4] and ch.seen[1] == ["h"]
    assert v.rule.id == 3 and v.how == DecisionHow.AI and v.why == "because"


async def test_ai_none_or_unknown_rule_is_no_rule():
    v = await decide([rule(1)], mail(), remembered={}, chooser=FakeChooser(None), hints=[])
    assert v.rule is None and v.how == DecisionHow.AI
    v = await decide([rule(1)], mail(), remembered={}, chooser=FakeChooser(99, raw="?"), hints=[])
    assert v.rule is None and v.raw == "?"


async def test_without_a_model_only_fixed_rules_run():
    v = await decide([rule(1)], mail(), remembered={}, chooser=None, hints=[])
    assert v.rule is None and v.how == DecisionHow.NONE and "No model" in v.why


async def test_remembered_rule_that_is_gone_is_ignored():
    v = await decide(
        [rule(1)],
        mail(),
        remembered={"news@letters.example.com": 7},
        chooser=FakeChooser(1),
        hints=[],
    )
    assert v.rule.id == 1 and v.how == DecisionHow.AI
