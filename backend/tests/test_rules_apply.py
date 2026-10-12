# backend/tests/test_rules_apply.py
import pytest
from sqlalchemy import select

from app.models import MailLabel, MailMessage, RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, MailState
from app.services.google.quota import BudgetReached
from app.services.mail.store import eml_path
from app.services.rules.apply import BUDGET_WAIT, plan_changes


def decision(gbox, row, actions, status=DecisionStatus.SUGGESTED):
    with gbox.sf() as db:
        d = RuleDecision(
            account_id=gbox.acc.id,
            message_id=row.id,
            how=DecisionHow.AI,
            actions=actions,
            labels_seen=list(row.labels),
            status=status,
            rule_name="R",
        )
        db.add(d)
        db.commit()
        return d.id


def get(gbox, model, id):
    with gbox.sf() as db:
        return db.get(model, id)


def test_plan_changes():
    p = plan_changes(
        [
            {"kind": "archive"},
            {"kind": "mark_read"},
            {"kind": "star"},
            {"kind": "label", "label": "News"},
        ],
        ["INBOX", "UNREAD"],
    )
    assert p.remove == ["INBOX", "UNREAD"] and p.add_names == ["STARRED", "News"] and not p.trash
    assert plan_changes([{"kind": "archive"}], ["SENT"]).remove == []


async def test_apply_labels_and_archive_records_changes_first(gbox):
    row = gbox.add("Digest", labels=("INBOX", "UNREAD"))
    did = decision(gbox, row, [{"kind": "label", "label": "Newsletters"}, {"kind": "archive"}])
    assert await gbox.applier.apply(did) == DecisionStatus.APPLIED
    d = get(gbox, RuleDecision, did)
    lid = next(k for k, v in gbox.fake.user_labels.items() if v == "Newsletters")
    assert d.changes == {"add": [lid], "remove": ["INBOX"], "trash": False}
    assert set(gbox.fake.messages[row.provider_id].labels) == {"UNREAD", lid}
    assert set(get(gbox, MailMessage, row.id).labels) == {"UNREAD", lid}  # local follows at once


async def test_label_names_match_existing_ignoring_case_and_spaces(gbox):
    gbox.fake.add_label("Newsletters")
    with gbox.sf() as db:
        db.add(
            MailLabel(
                account_id=gbox.acc.id, provider_label_id="Label_1", name="Newsletters", type="user"
            )
        )
        db.commit()
    a, b = gbox.add("One"), gbox.add("Two")
    for row in (a, b):
        await gbox.applier.apply(
            decision(gbox, row, [{"kind": "label", "label": "  newsletters "}])
        )
    assert list(gbox.fake.user_labels.values()) == ["Newsletters"]  # never created twice


async def test_label_created_elsewhere_meanwhile_is_found_after_409(gbox):
    gbox.fake.add_label("Work/Clients")  # in Gmail, but not yet in the local label list
    row = gbox.add("Hi")
    assert (
        await gbox.applier.apply(decision(gbox, row, [{"kind": "label", "label": "Work/Clients"}]))
        == "applied"
    )
    assert len(gbox.fake.user_labels) == 1


async def test_delete_checks_copy_trashes_and_archives_locally(gbox):
    row = gbox.add("Spam-ish")
    did = decision(gbox, row, [{"kind": "delete"}])
    assert await gbox.applier.apply(did) == "applied"
    assert "TRASH" in gbox.fake.messages[row.provider_id].labels
    assert get(gbox, MailMessage, row.id).state == MailState.LOCAL_ARCHIVE


async def test_delete_refuses_a_bad_copy_and_changes_nothing(gbox):
    row = gbox.add("Hi")
    eml_path(gbox.layout, gbox.acc.email, row.file_path).write_bytes(b"broken")
    did = decision(gbox, row, [{"kind": "delete"}])
    assert await gbox.applier.apply(did) == "failed"
    assert "nothing was deleted" in get(gbox, RuleDecision, did).reason
    assert "TRASH" not in gbox.fake.messages[row.provider_id].labels


async def test_mail_gone_from_gmail_fails_plainly(gbox):
    row = gbox.add("Hi")
    gbox.fake.remove(row.provider_id)
    did = decision(gbox, row, [{"kind": "archive"}])
    assert await gbox.applier.apply(did) == "failed"
    assert "no longer in Gmail" in get(gbox, RuleDecision, did).reason


async def test_budget_waits_without_failing(gbox, monkeypatch):
    row = gbox.add("Hi")
    did = decision(gbox, row, [{"kind": "archive"}])

    async def no_budget(*a, **k):
        raise BudgetReached(1000)

    monkeypatch.setattr(gbox.adapter, "modify", no_budget)
    with pytest.raises(BudgetReached):
        await gbox.applier.apply(did)
    d = get(gbox, RuleDecision, did)
    assert d.status == "suggested" and d.reason == BUDGET_WAIT


async def test_undo_reverses_only_what_is_still_as_left(gbox):
    row = gbox.add("Digest", labels=("INBOX", "UNREAD"))
    did = decision(gbox, row, [{"kind": "archive"}, {"kind": "mark_read"}])
    await gbox.applier.apply(did)
    with gbox.sf() as db:  # the owner marks it unread again in Gmail; the backup saw it
        m = db.get(MailMessage, row.id)
        m.labels = ["UNREAD"]
        db.commit()
    res = await gbox.applier.undo(did)
    assert res.status == "undone" and res.left == ["UNREAD"]
    assert "INBOX" in gbox.fake.messages[row.provider_id].labels


async def test_undo_delete_untrashes(gbox):
    row = gbox.add("Hi")
    did = decision(gbox, row, [{"kind": "delete"}])
    await gbox.applier.apply(did)
    await gbox.applier.undo(did)
    assert "TRASH" not in gbox.fake.messages[row.provider_id].labels
    assert get(gbox, MailMessage, row.id).state == MailState.IN_MAIL


async def test_delete_by_hand_is_logged_and_undoable(gbox):
    row = gbox.add("Hi")
    d = await gbox.applier.delete_by_hand(gbox.acc.id, row.id)
    assert d.how == "manual" and d.status == "applied"
    with gbox.sf() as db:
        assert (
            db.scalar(select(MailMessage).where(MailMessage.id == row.id)).state == "local_archive"
        )
