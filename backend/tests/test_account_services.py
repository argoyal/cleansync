import sqlite3

from sqlalchemy import select, text

from app.database.engine import make_engine
from app.database.migrate import run_migrations
from app.models import AccountService
from app.models.enums import ServiceKind, ServiceState
from app.services.accounts.services import (
    ensure_services,
    service_state,
    service_states,
    set_service_state,
)

DRIVE = "https://www.googleapis.com/auth/drive"


def test_service_rows_are_made_for_existing_accounts(tmp_path):
    db_file = tmp_path / "s.db"
    run_migrations(db_file, revision="0003")
    con = sqlite3.connect(db_file)
    con.execute(
        "INSERT INTO accounts (id, email, remote_name, status, connected_at) "
        "VALUES (1, 'alice@example.com', 'gd_a', 'CONNECTED', '2026-10-01')"
    )
    con.commit()
    con.close()
    run_migrations(db_file)
    engine = make_engine(db_file)
    with engine.connect() as c:
        assert c.execute(text("SELECT provider, scopes FROM accounts")).one() == ("google", DRIVE)
        rows = c.execute(text("SELECT kind, state FROM account_services ORDER BY kind")).all()
        assert rows == [("files", "active"), ("mail", "not_set_up")]
        assert c.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_ensure_services_adds_only_what_is_missing(db, make_account):
    acc = make_account()
    ensure_services(db, acc)
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    ensure_services(db, acc)  # never resets a state that is there
    assert service_states(db, acc.id) == {"files": "active", "mail": "active"}
    rows = db.scalars(select(AccountService).where(AccountService.account_id == acc.id)).all()
    assert len(rows) == 2


def test_set_service_state_keeps_a_note_and_makes_a_missing_row(db, make_account):
    acc = make_account()
    row = set_service_state(
        db, acc.id, ServiceKind.MAIL, ServiceState.NEEDS_RECONNECT, note="Google said no"
    )
    assert row.note == "Google said no"
    assert service_state(db, acc.id, ServiceKind.MAIL) == "needs_reconnect"
    assert service_state(db, acc.id, ServiceKind.FILES) == "not_set_up"  # no row yet


def test_accounts_api_shows_provider_and_services(auth_client, app, make_account):
    acc = make_account()
    with app.state.session_factory() as db:
        ensure_services(db, acc)
    body = auth_client.get("/api/accounts").json()[0]
    assert body["provider"] == "google"
    assert body["services"] == {"files": "active", "mail": "not_set_up"}
    assert body["mail_permission"] is False
