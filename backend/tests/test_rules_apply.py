# backend/tests/test_rules_apply.py
import asyncio

import pytest
from sqlalchemy import select

from app.models import MailLabel, MailMessage, RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, MailState
from app.services.google.client import RateLimited
from app.services.google.quota import BudgetReached
from app.services.mail.adapter import MailApiError, MailAuthError, MailUnreachable
from app.services.mail.store import eml_path
from app.services.rules.apply import (
    BUDGET_WAIT,
    RECONNECT_WAIT,
    UNREACHABLE_WAIT,
    is_system_label,
    plan_changes,
)


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


async def test_apply_labels_and_archive_records_changes_first(gbox, monkeypatch):
    row = gbox.add("Digest", labels=("INBOX", "UNREAD"))
    did = decision(gbox, row, [{"kind": "label", "label": "Newsletters"}, {"kind": "archive"}])
    real_modify, seen = gbox.adapter.modify, {}

    async def spy(pid, add, remove):
        seen["changes"] = get(gbox, RuleDecision, did).changes  # read at call time
        return await real_modify(pid, add, remove)

    monkeypatch.setattr(gbox.adapter, "modify", spy)
    assert await gbox.applier.apply(did) == DecisionStatus.APPLIED
    assert seen["changes"]["remove"] == ["INBOX"] and len(seen["changes"]["add"]) == 1
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
    results = []
    for row in (a, b):
        did = decision(gbox, row, [{"kind": "label", "label": "  newsletters "}])
        results.append(await gbox.applier.apply(did))
    assert results == ["applied", "applied"]
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
    assert (await gbox.applier.undo(d.id)).status == "undone"
    assert get(gbox, MailMessage, row.id).state == MailState.IN_MAIL
    assert "TRASH" not in gbox.fake.messages[row.provider_id].labels
    await gbox.applier.delete_by_hand(gbox.acc.id, row.id)
    with gbox.sf() as db:
        assert (
            db.scalar(select(MailMessage).where(MailMessage.id == row.id)).state == "local_archive"
        )


async def test_label_already_on_the_mail_is_not_undone(gbox):
    lid = gbox.fake.add_label("Receipts")
    with gbox.sf() as db:
        db.add(
            MailLabel(account_id=gbox.acc.id, provider_label_id=lid, name="Receipts", type="user")
        )
        db.commit()
    row = gbox.add("Bill", labels=("INBOX", lid))
    did = decision(gbox, row, [{"kind": "label", "label": "Receipts"}, {"kind": "archive"}])
    assert await gbox.applier.apply(did) == "applied"
    assert get(gbox, RuleDecision, did).changes["add"] == []
    assert (await gbox.applier.undo(did)).status == "undone"
    assert set(gbox.fake.messages[row.provider_id].labels) == {"INBOX", lid}


@pytest.mark.parametrize("name", ["TRASH", "spam", "Category_Promotions", " inbox ", "Unread"])
async def test_gmail_own_label_names_fail_and_change_nothing(gbox, name):
    assert is_system_label(name)
    row = gbox.add("Hi")
    did = decision(gbox, row, [{"kind": "label", "label": name}])
    assert await gbox.applier.apply(did) == "failed"
    assert "Gmail's own labels" in get(gbox, RuleDecision, did).reason
    assert set(gbox.fake.messages[row.provider_id].labels) == {"INBOX"}


async def test_a_user_label_named_like_a_system_row_is_not_matched(gbox):
    with gbox.sf() as db:  # a system row saved by the label sync
        db.add(
            MailLabel(account_id=gbox.acc.id, provider_label_id="CHAT", name="Chat", type="system")
        )
        db.commit()
    row = gbox.add("Hi")
    did = decision(gbox, row, [{"kind": "label", "label": "Chat"}])
    assert await gbox.applier.apply(did) == "failed"


@pytest.mark.parametrize(
    "error, wait",
    [
        (RateLimited(), BUDGET_WAIT),
        (MailUnreachable("down"), UNREACHABLE_WAIT),
        (MailAuthError("expired", login=False), RECONNECT_WAIT),
    ],
)
async def test_temporary_problems_wait_instead_of_failing(gbox, monkeypatch, error, wait):
    row = gbox.add("Hi")
    did = decision(gbox, row, [{"kind": "archive"}])

    async def boom(*a, **k):
        raise error

    monkeypatch.setattr(gbox.adapter, "modify", boom)
    with pytest.raises(type(error)):
        await gbox.applier.apply(did)
    d = get(gbox, RuleDecision, did)
    assert d.status == "suggested" and d.reason == wait


async def test_other_gmail_refusals_fail(gbox, monkeypatch):
    row = gbox.add("Hi")
    did = decision(gbox, row, [{"kind": "archive"}])

    async def refuse(*a, **k):
        raise MailApiError("Gmail said no.", 400)

    monkeypatch.setattr(gbox.adapter, "modify", refuse)
    assert await gbox.applier.apply(did) == "failed"
    assert get(gbox, RuleDecision, did).reason == "Gmail said no."


async def test_undo_waits_or_keeps_applied_with_a_reason(gbox, monkeypatch):
    row = gbox.add("Hi")
    did = decision(gbox, row, [{"kind": "archive"}])
    await gbox.applier.apply(did)

    async def down(*a, **k):
        raise MailUnreachable("down")

    monkeypatch.setattr(gbox.adapter, "modify", down)
    with pytest.raises(MailUnreachable):
        await gbox.applier.undo(did)
    d = get(gbox, RuleDecision, did)
    assert d.status == "applied" and d.reason == UNREACHABLE_WAIT

    async def refuse(*a, **k):
        raise MailApiError("Gmail said no.", 400)

    monkeypatch.setattr(gbox.adapter, "modify", refuse)
    res = await gbox.applier.undo(did)
    assert res.status == "applied" and res.left == []
    d = get(gbox, RuleDecision, did)
    assert d.status == "applied" and d.reason == "Couldn't undo: Gmail said no."


async def test_two_mails_with_the_same_new_label_make_one_label(gbox):
    a, b = gbox.add("One"), gbox.add("Two")
    d1 = decision(gbox, a, [{"kind": "label", "label": "Fresh"}])
    d2 = decision(gbox, b, [{"kind": "label", "label": "Fresh"}])
    r = await asyncio.gather(gbox.applier.apply(d1), gbox.applier.apply(d2))
    assert r == ["applied", "applied"]
    assert list(gbox.fake.user_labels.values()) == ["Fresh"]


async def test_missing_mail_row_fails_plainly(gbox):
    row = gbox.add("Hi")
    did = decision(gbox, row, [{"kind": "archive"}])
    with gbox.sf() as db:
        db.execute(MailMessage.__table__.delete().where(MailMessage.id == row.id))
        db.commit()
    # the decision goes with the row (cascade) on databases that enforce it; either way no crash
    result = await gbox.applier.apply(did)
    assert result in ("failed",)


@pytest.mark.parametrize(
    ("actions", "later"),
    [([{"kind": "archive"}], MailState.OFFLOADED), ([{"kind": "delete"}], MailState.DELETED)],
)
async def test_undo_of_mail_no_longer_in_gmail_changes_nothing(gbox, actions, later):
    row = gbox.add("Old")
    did = decision(gbox, row, actions)
    await gbox.applier.apply(did)
    with gbox.sf() as db:  # later offloaded, or deleted forever from the archive
        db.get(MailMessage, row.id).state = later
        db.commit()
    labels_before = list(gbox.fake.messages[row.provider_id].labels)
    res = await gbox.applier.undo(did)
    assert (res.status, res.left) == ("applied", [])
    d = get(gbox, RuleDecision, did)
    assert d.status == "applied" and d.reason == "It's no longer in Gmail, so nothing was undone."
    assert get(gbox, MailMessage, row.id).state == later
    assert gbox.fake.messages[row.provider_id].labels == labels_before
