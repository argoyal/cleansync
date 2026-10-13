"""Bulk offload spec §3: permanent delete is on only with the switch and the scope."""

from sqlalchemy import select

from app.models import Event
from app.services.accounts.oauth import DRIVE_SCOPE, FULL_MAIL_SCOPE, GMAIL_SCOPE, has_scope
from app.services.accounts.permanent import (
    LOST_MSG,
    allows_permanent_delete,
    permanent_delete_on,
    permission_lost,
    turn_off,
)
from app.services.accounts.services import mail_needs_reconnect
from app.services.events.bus import EventBus


def _acc(db, make_account, scopes, on=True):
    acc = make_account()
    acc.scopes, acc.permanent_delete = scopes, on
    db.commit()
    return acc


def test_on_needs_the_switch_and_the_scope(db, make_account):
    acc = _acc(db, make_account, f"{DRIVE_SCOPE} {GMAIL_SCOPE}")
    assert permanent_delete_on(acc) is False  # the switch alone isn't enough
    acc.scopes += f" {FULL_MAIL_SCOPE}"
    db.commit()
    assert permanent_delete_on(acc) is True and allows_permanent_delete(db, acc.id) is True
    acc.permanent_delete = False
    db.commit()
    assert permanent_delete_on(acc) is False
    assert permanent_delete_on(None) is False and allows_permanent_delete(db, 999) is False


def test_turning_it_off_keeps_the_scope_and_clears_the_note(db, make_account):
    acc = _acc(db, make_account, f"{GMAIL_SCOPE} {FULL_MAIL_SCOPE}")
    acc.permanent_delete_note = LOST_MSG
    db.commit()
    turn_off(db, acc.id)
    db.refresh(acc)
    assert acc.permanent_delete is False and acc.permanent_delete_note is None
    assert has_scope(acc.scopes, FULL_MAIL_SCOPE)  # CleanSync doesn't revoke it at Google


def test_losing_it_turns_the_switch_off_once_with_a_note(db, make_account):
    acc = _acc(db, make_account, f"{GMAIL_SCOPE} {FULL_MAIL_SCOPE}")
    bus = EventBus()
    permission_lost(db, bus, acc.id)
    db.refresh(acc)
    assert acc.permanent_delete is False and acc.permanent_delete_note == LOST_MSG
    assert not has_scope(acc.scopes, FULL_MAIL_SCOPE) and has_scope(acc.scopes, GMAIL_SCOPE)
    permission_lost(db, bus, acc.id)  # again: nothing more to say
    events = db.scalars(select(Event).where(Event.kind == "permanent_delete_lost")).all()
    assert [e.message for e in events] == [f"{acc.email}: {LOST_MSG}"]
    assert events[0].level == "warning"


def test_losing_the_mail_permission_loses_the_full_one_too(db, make_account):
    acc = _acc(db, make_account, f"{DRIVE_SCOPE} {GMAIL_SCOPE} {FULL_MAIL_SCOPE}")
    mail_needs_reconnect(db, acc.id, "gone", permission_gone=True)
    db.refresh(acc)
    assert acc.scopes == DRIVE_SCOPE and permanent_delete_on(acc) is False
