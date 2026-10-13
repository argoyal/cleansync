"""Settings → Accounts → Allow permanent delete (bulk offload spec §3)."""

from urllib.parse import parse_qs, urlparse

import respx

from app.models import Account
from app.models.enums import AccountStatus, ServiceKind, ServiceState
from app.services.accounts.oauth import DRIVE_SCOPE, FULL_MAIL_SCOPE, GMAIL_SCOPE
from app.services.accounts.permanent import LOST_MSG
from app.services.accounts.services import ensure_services, mail_needs_reconnect, set_service_state
from tests.test_mail_setup import _account, _google

BOTH = f"{DRIVE_SCOPE} {GMAIL_SCOPE}"


def _mail_ready(app, make_account, scopes=BOTH, on=False, email="alice@example.com"):
    acc = make_account(email)
    with app.state.session_factory() as db:
        ensure_services(db, acc)
    with app.state.session_factory() as db:
        row = db.get(Account, acc.id)
        row.scopes, row.permanent_delete = scopes, on
        db.commit()
        set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    return acc


def _switch(auth_client, acc, on: bool):
    return auth_client.post(f"/api/accounts/{acc.id}/permanent-delete", json={"on": on})


def _query(url):
    return parse_qs(urlparse(url).query)


def _me(auth_client):
    return auth_client.get("/api/accounts").json()[0]


@respx.mock
def test_turn_on_asks_google_for_the_full_permission_then_it_is_on(auth_client, app, make_account):
    acc = _mail_ready(app, make_account)
    r = _switch(auth_client, acc, True)
    assert r.status_code == 200, r.text
    q = _query(r.json()["auth_url"])
    assert {FULL_MAIL_SCOPE, GMAIL_SCOPE, DRIVE_SCOPE} <= set(q["scope"][0].split())
    _google(f"{BOTH} {FULL_MAIL_SCOPE}")
    back = auth_client.get(
        f"/api/oauth/callback?state={q['state'][0]}&code=c", follow_redirects=False
    )
    assert back.headers["location"] == f"/?connected={acc.id}&permanent=on"
    me = _me(auth_client)
    assert me["permanent_delete"] is True and me["permanent_delete_note"] is None
    assert me["services"]["mail"] == "active"


@respx.mock
def test_a_sign_in_without_the_box_ticked_leaves_it_off(auth_client, app, make_account):
    acc = _mail_ready(app, make_account)
    q = _query(_switch(auth_client, acc, True).json()["auth_url"])
    _google(BOTH)
    back = auth_client.get(
        f"/api/oauth/callback?state={q['state'][0]}&code=c", follow_redirects=False
    )
    assert back.headers["location"] == f"/?connected={acc.id}&permanent=denied"
    assert _me(auth_client)["permanent_delete"] is False
    assert _me(auth_client)["services"]["mail"] == "active"  # Mail keeps working


def test_a_plain_reconnect_never_asks_for_it(auth_client, app, make_account):
    acc = _mail_ready(app, make_account)
    url = auth_client.post(f"/api/accounts/{acc.id}/reconnect").json()["auth_url"]
    assert FULL_MAIL_SCOPE not in _query(url)["scope"][0].split()


def test_when_google_already_allows_it_no_sign_in_is_needed(auth_client, app, make_account):
    acc = _mail_ready(app, make_account, f"{BOTH} {FULL_MAIL_SCOPE}")
    assert _switch(auth_client, acc, True).json() == {"auth_url": None}
    assert _me(auth_client)["permanent_delete"] is True
    assert _switch(auth_client, acc, False).json() == {"auth_url": None}
    me = _me(auth_client)
    assert me["permanent_delete"] is False and me["permanent_delete_note"] is None
    with app.state.session_factory() as db:
        assert FULL_MAIL_SCOPE in db.get(Account, acc.id).scopes.split()  # not revoked


@respx.mock
def test_a_reconnect_that_lost_the_permission_turns_it_off_with_a_note(
    auth_client, app, make_account
):
    acc = _mail_ready(app, make_account, f"{BOTH} {FULL_MAIL_SCOPE}", on=True)
    url = auth_client.post(f"/api/accounts/{acc.id}/reconnect").json()["auth_url"]
    _google(BOTH)  # the owner removed it at Google meanwhile
    auth_client.get(
        f"/api/oauth/callback?state={_query(url)['state'][0]}&code=c", follow_redirects=False
    )
    me = _me(auth_client)
    assert me["permanent_delete"] is False and me["permanent_delete_note"] == LOST_MSG


def test_it_needs_mail_set_up(auth_client, app, make_account):
    acc = _account(app, make_account)  # Mail not set up
    r = _switch(auth_client, acc, True)
    assert r.status_code == 400
    assert r.json()["detail"] == "Set up mail backup for this account first."
    assert (
        auth_client.post("/api/accounts/999/permanent-delete", json={"on": True}).status_code == 404
    )


@respx.mock
def test_losing_mail_then_a_plain_sign_in_with_the_full_scope_does_not_turn_it_back_on(
    auth_client, app, make_account
):
    """Only the explicit Turn on sets the switch (controller ruling)."""
    acc = _mail_ready(app, make_account, f"{BOTH} {FULL_MAIL_SCOPE}", on=True)
    with app.state.session_factory() as db:
        mail_needs_reconnect(db, acc.id, "gone", permission_gone=True)
    with app.state.session_factory() as db:
        assert db.get(Account, acc.id).permanent_delete is False
    url = auth_client.post(f"/api/accounts/{acc.id}/reconnect").json()["auth_url"]
    _google(f"{BOTH} {FULL_MAIL_SCOPE}")  # Google still remembers the full scope
    auth_client.get(
        f"/api/oauth/callback?state={_query(url)['state'][0]}&code=c", follow_redirects=False
    )
    me = _me(auth_client)
    assert me["permanent_delete"] is False


@respx.mock
def test_turn_off_drops_a_waiting_turn_on_sign_in_so_an_old_tab_cannot_turn_it_back_on(
    auth_client, app, make_account
):
    acc = _mail_ready(app, make_account)
    q = _query(_switch(auth_client, acc, True).json()["auth_url"])  # a Google tab is left open
    assert _switch(auth_client, acc, False).json() == {"auth_url": None}
    _google(f"{BOTH} {FULL_MAIL_SCOPE}")
    back = auth_client.get(
        f"/api/oauth/callback?state={q['state'][0]}&code=c", follow_redirects=False
    )
    assert back.headers["location"].startswith("/?oauth_error=")  # the sign-in is used up
    assert _me(auth_client)["permanent_delete"] is False


@respx.mock
def test_turn_off_keeps_another_accounts_waiting_sign_in(auth_client, app, make_account):
    one = _mail_ready(app, make_account)
    two = _mail_ready(app, make_account, email="bob@example.com")
    q = _query(_switch(auth_client, two, True).json()["auth_url"])
    _switch(auth_client, one, False)
    _google(f"{BOTH} {FULL_MAIL_SCOPE}", email=two.email)
    back = auth_client.get(
        f"/api/oauth/callback?state={q['state'][0]}&code=c", follow_redirects=False
    )
    assert back.headers["location"] == f"/?connected={two.id}&permanent=on"


def test_turn_on_and_turn_off_both_tell_the_pages_the_account_status(
    auth_client, app, make_account, monkeypatch
):
    acc = _mail_ready(app, make_account, f"{BOTH} {FULL_MAIL_SCOPE}")
    seen = []
    real = app.state.bus.publish
    monkeypatch.setattr(
        app.state.bus, "publish", lambda kind, data: (seen.append((kind, data)), real(kind, data))
    )
    _switch(auth_client, acc, True)
    _switch(auth_client, acc, False)
    events = [d for k, d in seen if k == "account"]
    assert len(events) == 2
    assert all(e["account_id"] == acc.id and e["status"] == AccountStatus.CONNECTED for e in events)


def test_turn_on_is_refused_for_an_account_that_is_not_connected(auth_client, app, make_account):
    acc = _mail_ready(app, make_account, f"{BOTH} {FULL_MAIL_SCOPE}")
    with app.state.session_factory() as db:
        db.get(Account, acc.id).status = AccountStatus.DISCONNECTED
        db.commit()
    r = _switch(auth_client, acc, True)
    assert r.status_code == 400
    assert r.json()["detail"] == "Connect this Google account again first (Settings, Accounts)."
    with app.state.session_factory() as db:
        assert db.get(Account, acc.id).permanent_delete is False


def test_turn_on_for_a_pretend_local_account_needs_no_google(auth_client, app, settings):
    import asyncio

    settings.allow_local_accounts = True
    folder = app.state.layout.root / "pretend"
    acc = asyncio.run(app.state.accounts.add_local_account("bob@example.com", folder))
    assert auth_client.post(f"/api/accounts/{acc.id}/mail/setup").json() == {"auth_url": None}
    r = _switch(auth_client, acc, True)
    assert r.status_code == 200 and r.json() == {"auth_url": None}
    me = _me(auth_client)
    assert me["permanent_delete"] is True and me["permanent_delete_note"] is None
    assert _switch(auth_client, acc, False).json() == {"auth_url": None}
    assert _me(auth_client)["permanent_delete"] is False
