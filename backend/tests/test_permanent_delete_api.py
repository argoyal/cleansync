"""Settings → Accounts → Allow permanent delete (bulk offload spec §3)."""

from urllib.parse import parse_qs, urlparse

import respx

from app.models import Account
from app.models.enums import ServiceKind, ServiceState
from app.services.accounts.oauth import DRIVE_SCOPE, FULL_MAIL_SCOPE, GMAIL_SCOPE
from app.services.accounts.permanent import LOST_MSG
from app.services.accounts.services import mail_needs_reconnect, set_service_state
from tests.test_mail_setup import _account, _google

BOTH = f"{DRIVE_SCOPE} {GMAIL_SCOPE}"


def _mail_ready(app, make_account, scopes=BOTH, on=False):
    acc = _account(app, make_account)
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
