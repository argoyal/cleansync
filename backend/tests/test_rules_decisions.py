from sqlalchemy import select

from app.models import RuleCorrection, RuleDecision, RuleSender
from app.models.enums import DecisionHow, DecisionStatus
from app.services.mail.index import apply_labels
from app.services.rules.decisions import (
    Decisions,
    hints_for,
    remembered_senders,
)
from app.services.rules.feedback import moved_back
from app.services.rules.store import RuleIn, create_rule, update_rule

ARCHIVE = RuleIn(name="News", description="digests", actions=[{"kind": "archive"}])
STAR = RuleIn(name="Important", description="from people", actions=[{"kind": "star"}])


def suggest(gbox, row, rule):
    with gbox.sf() as db:
        d = RuleDecision(
            account_id=gbox.acc.id,
            message_id=row.id,
            rule_id=rule.id,
            rule_version=rule.version,
            rule_name=rule.name,
            how=DecisionHow.AI,
            actions=list(rule.actions),
            labels_seen=list(row.labels),
            status=DecisionStatus.SUGGESTED,
        )
        db.add(d)
        db.commit()
        return d.id


def rules(gbox, *data):
    with gbox.sf() as db:
        return [create_rule(db, gbox.acc.id, x, "admin") for x in data]


async def test_three_approvals_remember_the_sender(gbox):
    (news,) = rules(gbox, ARCHIVE)
    dec = Decisions(gbox.sf, gbox.applier)
    for i in range(3):
        row = gbox.add(f"Digest {i}", sender="news@letters.example.com")
        await dec.approve(gbox.acc.id, [suggest(gbox, row, news)])
    with gbox.sf() as db:
        assert remembered_senders(db, gbox.acc.id) == {"news@letters.example.com": news.id}


async def test_approve_uses_the_recorded_actions_of_an_older_version(gbox):
    (news,) = rules(gbox, ARCHIVE)
    row = gbox.add("Digest")
    did = suggest(gbox, row, news)
    with gbox.sf() as db:  # the rule is edited to star instead, after the suggestion
        update_rule(
            db,
            gbox.acc.id,
            news.id,
            RuleIn(name="News", description="d", actions=[{"kind": "star"}]),
            "admin",
        )
    await Decisions(gbox.sf, gbox.applier).approve(gbox.acc.id, [did])
    assert "INBOX" not in gbox.fake.messages[row.provider_id].labels
    assert "STARRED" not in gbox.fake.messages[row.provider_id].labels


async def test_correct_an_applied_decision_undoes_then_applies_the_other_rule(gbox):
    news, important = rules(gbox, ARCHIVE, STAR)
    row = gbox.add("From a friend", sender="friend@example.com")
    dec = Decisions(gbox.sf, gbox.applier)
    did = suggest(gbox, row, news)
    await dec.approve(gbox.acc.id, [did])
    new_id = await dec.correct(gbox.acc.id, did, important.id)
    labels = gbox.fake.messages[row.provider_id].labels
    assert "INBOX" in labels and "STARRED" in labels
    with gbox.sf() as db:
        assert db.get(RuleDecision, did).status == "undone"
        assert db.get(RuleDecision, new_id).status == "applied"
        c = db.scalar(select(RuleCorrection))
        assert (c.from_rule, c.to_rule, c.sender) == ("News", "Important", "friend@example.com")
        assert "News" in hints_for(db, gbox.acc.id, "friend@example.com")[0]
        assert db.scalar(select(RuleSender).where(RuleSender.rule_id == news.id)) is None


async def test_correct_to_none_rejects_a_suggestion(gbox):
    (news,) = rules(gbox, ARCHIVE)
    row = gbox.add("Hi")
    did = suggest(gbox, row, news)
    assert await Decisions(gbox.sf, gbox.applier).correct(gbox.acc.id, did, None) is None
    with gbox.sf() as db:
        assert db.get(RuleDecision, did).status == "rejected"
    assert "INBOX" in gbox.fake.messages[row.provider_id].labels


async def test_moving_mail_back_to_the_inbox_is_noted(gbox):
    (news,) = rules(gbox, ARCHIVE)
    row = gbox.add("Digest")
    did = suggest(gbox, row, news)
    await Decisions(gbox.sf, gbox.applier).approve(gbox.acc.id, [did])
    with gbox.sf() as db:  # the backup sees the owner put it back
        apply_labels(db, gbox.acc.id, row.provider_id, ["INBOX"])
        assert db.get(RuleDecision, did).moved_back_at is not None
        assert moved_back(db, row.id)


async def test_owner_archiving_withdraws_a_suggestion(gbox):
    (news,) = rules(gbox, ARCHIVE)
    row = gbox.add("Digest")
    did = suggest(gbox, row, news)
    with gbox.sf() as db:
        apply_labels(db, gbox.acc.id, row.provider_id, [])
        d = db.get(RuleDecision, did)
        assert d.status == "withdrawn" and "yourself" in d.reason


async def test_senders_and_hints_never_cross_mailboxes(gbox, make_account, session_factory):
    (news,) = rules(gbox, ARCHIVE)
    dec = Decisions(gbox.sf, gbox.applier)
    for i in range(3):
        row = gbox.add(f"D{i}", sender="news@letters.example.com")
        await dec.approve(gbox.acc.id, [suggest(gbox, row, news)])
    other = make_account("bob@example.com")
    with session_factory() as db:
        assert remembered_senders(db, other.id) == {}
        assert hints_for(db, other.id, "news@letters.example.com") == []


async def test_ids_of_another_mailbox_are_ignored(gbox, make_account):
    (news,) = rules(gbox, ARCHIVE)
    did = suggest(gbox, gbox.add("Hi"), news)
    other = make_account("bob@example.com")
    counts = await Decisions(gbox.sf, gbox.applier).approve(other.id, [did])
    assert counts == {}
