"""Which services (Files, Mail) an account has, and in what state (spec §3.1)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import Account, AccountService
from app.models.enums import ServiceKind, ServiceState

# A new Google account: every sign-in includes Drive, so Files works straight away; Mail
# waits for the owner to set it up.
DEFAULT_STATES: dict[str, str] = {
    ServiceKind.FILES: ServiceState.ACTIVE,
    ServiceKind.MAIL: ServiceState.NOT_SET_UP,
}


def get_service(db: Session, account_id: int, kind: str) -> AccountService | None:
    return db.scalar(
        select(AccountService).where(
            AccountService.account_id == account_id, AccountService.kind == kind
        )
    )


def service_state(db: Session, account_id: int, kind: str) -> str:
    row = get_service(db, account_id, kind)
    return ServiceState.NOT_SET_UP if row is None else row.state


def service_states(db: Session, account_id: int) -> dict[str, str]:
    return {kind.value: service_state(db, account_id, kind) for kind in ServiceKind}


def ensure_services(db: Session, account: Account) -> None:
    """Add the rows a new account is missing. A state that is there is never changed."""
    have = set(
        db.scalars(select(AccountService.kind).where(AccountService.account_id == account.id))
    )
    for kind, state in DEFAULT_STATES.items():
        if kind not in have:
            db.add(AccountService(account_id=account.id, kind=kind, state=state))
    db.commit()


def set_service_state(
    db: Session, account_id: int, kind: str, state: str, note: str | None = None
) -> AccountService:
    row = get_service(db, account_id, kind)
    if row is None:
        row = AccountService(account_id=account_id, kind=kind)
        db.add(row)
    row.state = state
    row.note = note
    row.updated_at = utcnow()
    db.commit()
    return row
