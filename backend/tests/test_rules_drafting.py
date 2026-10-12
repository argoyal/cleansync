from datetime import UTC, datetime

from app.models.enums import RuleCombine
from app.services.ai.client import AiAnswer
from app.services.ai.connections import AiTarget
from app.services.rules.drafting import rule_from_mails, sentence_to_rule, try_rule
from app.services.rules.mailview import MailForModel
from app.services.rules.match import Choice, Conditions, RuleView

T = AiTarget(1, "http://m.example.com/v1", None, {}, "m", 0.0, 5.0)


class Client:
    def __init__(self, data):
        self.data, self.prompt = data, None

    async def ask(self, target, **kw):
        self.prompt = kw["user"]
        return AiAnswer(self.data, "", "json_object", 1, "m")


def mail(i, sender="news@letters.example.com", unsub=True):
    return MailForModel(
        i,
        sender,
        "",
        ["alice@example.com"],
        [],
        "to",
        f"Issue {i}",
        datetime(2026, 10, 1, tzinfo=UTC),
        [],
        unsub,
        unsub,
        False,
        "text",
        ["INBOX"],
    )


async def test_sentence_becomes_a_draft_with_conditions_and_actions():
    c = Client(
        {
            "name": "Old newsletters",
            "description": "Newsletters I signed up for",
            "actions": ["label", "archive", "send_money"],
            "label": "Newsletters",
            "senders_not_in": ["substack.com"],
            "list_unsubscribe": True,
        }
    )
    d = await sentence_to_rule(c, T, "archive newsletters, except from Substack")
    assert d.name == "Old newsletters" and d.combine == RuleCombine.FIXED_THEN_AI
    assert d.conditions == {"senders_not_in": ["substack.com"], "list_unsubscribe": True}
    assert d.actions == [{"kind": "label", "label": "Newsletters"}, {"kind": "archive"}]
    assert any("send_money" in n for n in d.notes)
    assert "archive newsletters" in c.prompt


async def test_without_a_model_the_sentence_is_the_description():
    d = await sentence_to_rule(Client(None), None, "Shipping updates from shops")
    assert d.description == "Shipping updates from shops" and d.actions == []
    assert d.notes and "No model" in d.notes[0]


async def test_from_mails_fills_in_what_they_share():
    c = Client({"name": "Letters", "description": "Issues of the Letters newsletter"})
    d = await rule_from_mails(c, T, [mail(1), mail(2)])
    assert d.conditions == {"senders_in": ["news@letters.example.com"], "list_unsubscribe": True}
    assert d.description == "Issues of the Letters newsletter"
    d2 = await rule_from_mails(
        c, T, [mail(1), mail(2, sender="other@letters.example.com", unsub=False)]
    )
    assert d2.conditions == {"senders_in": ["letters.example.com"]}


async def test_try_puts_the_new_rule_first():
    existing = [RuleView(5, 1, "Old", "old", Conditions(), "ai_only")]
    new = RuleView(-1, 0, "New", "new", Conditions(), "ai_only")

    class Ch:
        async def choose(self, rules, m, hints):
            assert [r.id for r in rules] == [-1, 5]
            return Choice(-1 if m.message_id == 1 else 5, "because")

    out = await try_rule(new, existing, [mail(1), mail(2)], Ch(), {}, lambda s: [])
    assert [(r.message_id, r.this_rule, r.rule_name) for r in out] == [
        (1, True, "New"),
        (2, False, "Old"),
    ]
