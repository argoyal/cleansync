"""Allow permanent delete (bulk offload spec §3): opt-in per mailbox. On only while the
owner's switch is on and Google granted the full Gmail permission."""

from sqlalchemy.orm import Session

from app.models import Account
from app.services.accounts.oauth import FULL_MAIL_SCOPE, has_scope
from app.services.events.bus import EventBus, record_event

NOT_ALLOWED_MSG = "Turn on Allow permanent delete in Settings → Accounts."
LOST_MSG = "Permanent delete was turned off: Google no longer allows it for this mailbox."


def permanent_delete_on(acc: Account | None) -> bool:
    return acc is not None and bool(acc.permanent_delete) and has_scope(acc.scopes, FULL_MAIL_SCOPE)


def allows_permanent_delete(db: Session, account_id: int) -> bool:
    return permanent_delete_on(db.get(Account, account_id))


def turn_off(db: Session, account_id: int) -> None:
    """The owner's Turn off: CleanSync stops using it. Google keeps the permission until
    the owner removes it there (spec §3)."""
    acc = db.get(Account, account_id)
    if acc is not None:
        acc.permanent_delete, acc.permanent_delete_note = False, None
        db.commit()


def permission_lost(db: Session, bus: EventBus, account_id: int) -> None:
    """Google no longer allows it: the switch goes off with a note, the scope leaves the
    saved list (so Turn on asks Google again), and Activity says so once."""
    acc = db.get(Account, account_id)
    if acc is None:
        return
    was_on = bool(acc.permanent_delete)
    acc.permanent_delete = False
    acc.scopes = " ".join(s for s in acc.scopes.split() if s != FULL_MAIL_SCOPE)
    if was_on:
        acc.permanent_delete_note = LOST_MSG
    db.commit()
    if was_on:
        record_event(
            db,
            bus,
            kind="permanent_delete_lost",
            level="warning",
            message=f"{acc.email}: {LOST_MSG}",
            account_id=acc.id,
        )
        bus.publish("account", {"account_id": acc.id, "status": acc.status})
