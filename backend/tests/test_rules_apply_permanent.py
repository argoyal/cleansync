"""Bulk offload spec §4: a rule's Offload and Offload on one mail use messages.delete when
the mailbox allows permanent delete, messages.trash otherwise."""

import pytest
from sqlalchemy import select

from app.models import Account, Event, MailMessage, RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, MailState
from app.services.accounts.oauth import FULL_MAIL_SCOPE
from app.services.accounts.permanent import LOST_MSG
from app.services.mail.listing import decision_chips
from app.services.rules import apply as apply_mod
from app.services.rules.apply import NOT_IN_MAIL_MSG, NOT_UNDOABLE_MSG, NotInGmail
from tests.test_rules_apply import decision, get


def allow(gbox):
    with gbox.sf() as db:
        acc = db.get(Account, gbox.acc.id)
        acc.scopes, acc.permanent_delete = FULL_MAIL_SCOPE, True
        db.commit()


async def test_offload_removes_mail_for_good_when_allowed_and_offers_no_undo(gbox):
    allow(gbox)
    row = gbox.add("Promo")
    did = decision(gbox, row, [{"kind": "offload"}])
    assert await gbox.applier.apply(did) == DecisionStatus.APPLIED
    assert row.provider_id not in gbox.fake.messages
    changes = get(gbox, RuleDecision, did).changes
    assert changes["removed"] is True and changes["trash"] is True
    assert get(gbox, MailMessage, row.id).state == MailState.OFFLOADED
    res = await gbox.applier.undo(did)
    assert res.status == DecisionStatus.APPLIED and res.left == []
    assert get(gbox, RuleDecision, did).reason == NOT_UNDOABLE_MSG
    assert row.provider_id not in gbox.fake.messages


async def test_offload_without_it_still_uses_the_trash_and_can_be_undone(gbox):
    row = gbox.add("Promo")
    did = decision(gbox, row, [{"kind": "offload"}])
    assert await gbox.applier.apply(did) == DecisionStatus.APPLIED
    assert "TRASH" in gbox.fake.messages[row.provider_id].labels
    assert "removed" not in get(gbox, RuleDecision, did).changes
    assert (await gbox.applier.undo(did)).status == DecisionStatus.UNDONE


async def test_a_refused_full_permission_falls_back_to_the_trash(gbox):
    allow(gbox)
    gbox.fake.fail("DELETE", r"/messages/", 403, reason="insufficientPermissions")
    row = gbox.add("Promo")
    did = decision(gbox, row, [{"kind": "offload"}])
    assert await gbox.applier.apply(did) == DecisionStatus.APPLIED
    assert "TRASH" in gbox.fake.messages[row.provider_id].labels
    assert "removed" not in get(gbox, RuleDecision, did).changes
    with gbox.sf() as db:
        acc = db.get(Account, gbox.acc.id)
        assert acc.permanent_delete is False and acc.permanent_delete_note == LOST_MSG
        assert (
            len(db.scalars(select(Event).where(Event.kind == "permanent_delete_lost")).all()) == 1
        )


def test_chips_say_when_undo_cant_help_and_when_it_deletes_forever(gbox):
    a, b, c = gbox.add("A"), gbox.add("B"), gbox.add("C")
    with gbox.sf() as db:
        for row, actions, status, changes in (
            (a, [{"kind": "offload"}], DecisionStatus.APPLIED, {"trash": True, "removed": True}),
            (b, [{"kind": "offload"}], DecisionStatus.APPLIED, {"trash": True}),
            (c, [{"kind": "delete_forever"}], DecisionStatus.SUGGESTED, None),
        ):
            db.add(
                RuleDecision(
                    account_id=gbox.acc.id,
                    message_id=row.id,
                    how=DecisionHow.AI,
                    actions=actions,
                    status=status,
                    changes=changes,
                    rule_name="R",
                )
            )
        db.commit()
        chips = decision_chips(db, [a.id, b.id, c.id])
    assert chips[a.id]["undoable"] is False and chips[b.id]["undoable"] is True
    assert chips[c.id]["forever"] is True and chips[a.id]["forever"] is False


async def test_a_crash_after_gmail_removed_the_mail_still_leaves_the_removed_mark(
    gbox, monkeypatch
):
    """The mark is saved with the changes, before the Gmail call: a crash between the call
    and the final commit must not leave an Undo that can't work."""
    allow(gbox)
    row = gbox.add("Promo")
    did = decision(gbox, row, [{"kind": "offload"}])

    def crash(*a, **kw):
        raise RuntimeError("power cut")

    monkeypatch.setattr(apply_mod, "set_flags", crash)
    with pytest.raises(RuntimeError):
        await gbox.applier.apply(did)
    assert row.provider_id not in gbox.fake.messages  # Gmail did remove it
    d = get(gbox, RuleDecision, did)
    assert d.status != DecisionStatus.APPLIED and d.changes["removed"] is True


async def test_the_removed_mark_is_cleared_when_gmail_refuses_the_delete(gbox):
    allow(gbox)
    gbox.fake.fail("DELETE", r"/messages/", 400)
    row = gbox.add("Promo")
    did = decision(gbox, row, [{"kind": "offload"}])
    assert await gbox.applier.apply(did) == DecisionStatus.FAILED
    assert row.provider_id in gbox.fake.messages
    assert "removed" not in get(gbox, RuleDecision, did).changes


async def test_the_removed_mark_is_cleared_when_gmail_cannot_be_reached(gbox):
    from app.services.mail.adapter import MailUnreachable

    allow(gbox)
    row = gbox.add("Promo")
    did = decision(gbox, row, [{"kind": "offload"}])

    async def down(pid):
        raise MailUnreachable("down")

    gbox.adapter.delete = down
    with pytest.raises(MailUnreachable):
        await gbox.applier.apply(did)
    assert "removed" not in get(gbox, RuleDecision, did).changes


async def test_offload_by_hand_on_mail_that_is_not_in_gmail_is_refused_before_a_decision(gbox):
    row = gbox.add("Promo")
    with gbox.sf() as db:
        db.get(MailMessage, row.id).state = MailState.OFFLOADED
        db.commit()
    with pytest.raises(NotInGmail) as e:
        await gbox.applier.offload_by_hand(gbox.acc.id, row.id)
    assert str(e.value) == NOT_IN_MAIL_MSG
    with gbox.sf() as db:
        assert db.scalar(select(RuleDecision)) is None  # no failed chip is left behind
    assert [c for c in gbox.fake.calls if c[0] != "GET"] == []  # nothing was sent to Gmail


async def test_the_offloaded_by_you_chip_says_so_and_can_be_undone(gbox):
    row = gbox.add("Hi")
    d = await gbox.applier.offload_by_hand(gbox.acc.id, row.id)
    with gbox.sf() as db:
        chip = decision_chips(db, [row.id])[row.id]
    assert chip["rule_name"] == "Offloaded by you"
    assert chip["id"] == d.id and chip["how"] == DecisionHow.MANUAL
    assert chip["status"] == DecisionStatus.APPLIED and chip["undoable"] is True
