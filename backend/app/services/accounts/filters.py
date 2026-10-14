"""Allow Gmail filters (0.5.2 spec §5): opt-in per mailbox. On only while the owner's switch
is on and Google granted the Gmail settings permission."""

from sqlalchemy.orm import Session

from app.models import Account
from app.services.accounts.oauth import SETTINGS_SCOPE, has_scope
from app.services.events.bus import EventBus, record_event

NOT_ALLOWED_MSG = "Turn on Allow Gmail filters in Settings → Accounts."
LOST_MSG = "Gmail filters were turned off: Google no longer allows them for this mailbox."


def gmail_filters_on(acc: Account | None) -> bool:
    return acc is not None and bool(acc.gmail_filters) and has_scope(acc.scopes, SETTINGS_SCOPE)


def allows_gmail_filters(db: Session, account_id: int) -> bool:
    return gmail_filters_on(db.get(Account, account_id))


def turn_off(db: Session, account_id: int) -> None:
    """The owner's Turn off: CleanSync stops using it. Google keeps the permission until
    the owner removes it there."""
    acc = db.get(Account, account_id)
    if acc is not None:
        acc.gmail_filters, acc.gmail_filters_note = False, None
        db.commit()


def permission_lost(db: Session, bus: EventBus, account_id: int) -> None:
    """Google no longer allows it: the switch goes off with a note, the scope leaves the
    saved list (so Turn on asks Google again), and Activity says so once."""
    acc = db.get(Account, account_id)
    if acc is None:
        return
    was_on = bool(acc.gmail_filters)
    acc.gmail_filters = False
    acc.scopes = " ".join(s for s in acc.scopes.split() if s != SETTINGS_SCOPE)
    if was_on:
        acc.gmail_filters_note = LOST_MSG
    db.commit()
    if was_on:
        record_event(
            db,
            bus,
            kind="gmail_filters_lost",
            level="warning",
            message=f"{acc.email}: {LOST_MSG}",
            account_id=acc.id,
        )
        bus.publish("account", {"account_id": acc.id, "status": acc.status})
