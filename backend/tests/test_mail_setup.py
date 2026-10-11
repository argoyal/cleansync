from urllib.parse import parse_qs, urlparse

import httpx
import respx

from app.models.enums import ServiceKind, ServiceState
from app.services.accounts.oauth import ABOUT_URL, DRIVE_SCOPE, GMAIL_SCOPE, TOKEN_URL
from app.services.accounts.services import ensure_services, service_state, set_service_state


def _google(scope: str | None, email="alice@example.com"):
    token = {
        "access_token": "ya29.test",
        "refresh_token": "1//refresh-test-token-value",
        "expires_in": 3599,
        "token_type": "Bearer",
    }
    if scope is not None:
        token["scope"] = scope
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token))
    respx.get(ABOUT_URL).mock(
        return_value=httpx.Response(
            200, json={"user": {"emailAddress": email}, "storageQuota": {"usage": "1"}}
        )
    )


def _account(app, make_account):
    acc = make_account()
    with app.state.session_factory() as db:
        ensure_services(db, acc)
    return acc


def _mail_state(app, acc):
    with app.state.session_factory() as db:
        return service_state(db, acc.id, ServiceKind.MAIL)


def _setup_state(auth_client, acc) -> str:
    r = auth_client.post(f"/api/accounts/{acc.id}/mail/setup")
    assert r.status_code == 200, r.text
    url = r.json()["auth_url"]
    q = parse_qs(urlparse(url).query)
    assert GMAIL_SCOPE in q["scope"][0]
    assert q["include_granted_scopes"] == ["true"]
    return q["state"][0]


@respx.mock
def test_mail_setup_with_gmail_permission_gets_mail_ready(auth_client, app, make_account):
    acc = _account(app, make_account)
    state = _setup_state(auth_client, acc)
    _google(f"{DRIVE_SCOPE} {GMAIL_SCOPE}")
    r = auth_client.get(f"/api/oauth/callback?state={state}&code=c", follow_redirects=False)
    assert r.headers["location"] == f"/?connected={acc.id}&mail=ready"
    assert _mail_state(app, acc) == ServiceState.SETTING_UP
    body = auth_client.get("/api/accounts").json()[0]
    assert body["mail_permission"] is True and body["services"]["files"] == "active"


@respx.mock
def test_mail_setup_without_gmail_permission_changes_nothing(
    auth_client, app, make_account, fake_rclone
):
    acc = _account(app, make_account)
    state = _setup_state(auth_client, acc)
    _google(DRIVE_SCOPE)  # the Gmail box was left unticked on Google's screen
    r = auth_client.get(f"/api/oauth/callback?state={state}&code=c", follow_redirects=False)
    assert r.headers["location"] == f"/?connected={acc.id}&mail=denied"
    assert _mail_state(app, acc) == ServiceState.NOT_SET_UP
    body = auth_client.get("/api/accounts").json()[0]
    assert body["status"] == "CONNECTED"  # Drive keeps working
    assert body["mail_permission"] is False
    # The saved login was replaced by the Drive-only one, never removed.
    calls = fake_rclone.calls()
    assert any(c[4:6] == ["config", "create"] for c in calls)
    assert not any(c[4:6] == ["config", "delete"] for c in calls)


def test_a_cancelled_mail_setup_changes_nothing(auth_client, app, make_account):
    acc = _account(app, make_account)
    state = _setup_state(auth_client, acc)
    r = auth_client.get(
        f"/api/oauth/callback?state={state}&error=access_denied", follow_redirects=False
    )
    assert "cancelled" in r.headers["location"]
    assert _mail_state(app, acc) == ServiceState.NOT_SET_UP


@respx.mock
def test_a_drive_reconnect_that_drops_gmail_marks_mail_for_reconnecting(
    auth_client, app, make_account
):
    acc = _account(app, make_account)
    with app.state.session_factory() as db:
        set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    url = auth_client.post(f"/api/accounts/{acc.id}/reconnect").json()["auth_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    _google(DRIVE_SCOPE)
    r = auth_client.get(f"/api/oauth/callback?state={state}&code=c", follow_redirects=False)
    assert r.headers["location"] == f"/?connected={acc.id}"
    assert _mail_state(app, acc) == ServiceState.NEEDS_RECONNECT
    # Signing in again with Gmail ticked puts it back to work.
    state = _setup_state(auth_client, acc)
    _google(f"{DRIVE_SCOPE} {GMAIL_SCOPE}")
    auth_client.get(f"/api/oauth/callback?state={state}&code=c", follow_redirects=False)
    assert _mail_state(app, acc) == ServiceState.ACTIVE


def test_mail_setup_on_a_pretend_account_needs_no_google(auth_client, app, settings):
    import asyncio

    settings.allow_local_accounts = True
    folder = app.state.layout.root / "pretend"
    acc = asyncio.run(app.state.accounts.add_local_account("bob@example.com", folder))
    r = auth_client.post(f"/api/accounts/{acc.id}/mail/setup")
    assert r.status_code == 200 and r.json() == {"auth_url": None}
    assert _mail_state(app, acc) == ServiceState.SETTING_UP
    assert auth_client.get("/api/accounts").json()[0]["mail_permission"] is True


def test_mail_setup_when_gmail_is_already_allowed_needs_no_google(auth_client, app, make_account):
    acc = _account(app, make_account)
    with app.state.session_factory() as db:
        db.get(type(acc), acc.id).scopes = f"{DRIVE_SCOPE} {GMAIL_SCOPE}"
        db.commit()
    r = auth_client.post(f"/api/accounts/{acc.id}/mail/setup")
    assert r.status_code == 200 and r.json() == {"auth_url": None}
    assert _mail_state(app, acc) == ServiceState.SETTING_UP


def test_mail_setup_without_gmail_allowed_yet_asks_google(auth_client, app, make_account):
    acc = _account(app, make_account)
    with app.state.session_factory() as db:
        db.get(type(acc), acc.id).scopes = DRIVE_SCOPE
        db.commit()
    _setup_state(auth_client, acc)  # a Google sign-in link with Gmail in it
    assert _mail_state(app, acc) == ServiceState.NOT_SET_UP


def test_mail_setup_for_an_unknown_account_is_404(auth_client):
    assert auth_client.post("/api/accounts/999/mail/setup").status_code == 404


def _creates(fake_rclone) -> int:
    return len([c for c in fake_rclone.calls() if c[4:6] == ["config", "create"]])


@respx.mock
def test_a_sign_in_without_drive_changes_nothing_for_an_existing_account(
    auth_client, app, make_account, fake_rclone
):
    acc = _account(app, make_account)
    state = _setup_state(auth_client, acc)
    before = _creates(fake_rclone)
    _google(GMAIL_SCOPE)  # Drive was left unticked on Google's screen
    r = auth_client.get(f"/api/oauth/callback?state={state}&code=c", follow_redirects=False)
    assert "oauth_error=" in r.headers["location"] and "Drive" in r.headers["location"]
    assert _creates(fake_rclone) == before
    assert _mail_state(app, acc) == ServiceState.NOT_SET_UP
    body = auth_client.get("/api/accounts").json()[0]
    assert body["status"] == "CONNECTED"
    assert body["mail_permission"] is False
    assert body["services"]["files"] == "active"


@respx.mock
def test_a_new_sign_in_without_drive_adds_no_account(auth_client, fake_rclone):
    url = auth_client.post("/api/accounts/connect").json()["auth_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    _google(GMAIL_SCOPE, email="carol@example.com")
    r = auth_client.get(f"/api/oauth/callback?state={state}&code=c", follow_redirects=False)
    assert "oauth_error=" in r.headers["location"] and "Drive" in r.headers["location"]
    assert _creates(fake_rclone) == 0
    assert auth_client.get("/api/accounts").json() == []


def test_reconnect_mail_after_gmail_refused_asks_google_again(auth_client, app, make_account):
    # Gmail answered 403 missing-permission: Mail needs reconnecting even though the saved
    # scopes still list Gmail. Reconnect mail must go through Google, not flip Mail back on.
    acc = _account(app, make_account)
    with app.state.session_factory() as db:
        db.get(type(acc), acc.id).scopes = f"{DRIVE_SCOPE} {GMAIL_SCOPE}"
        db.commit()
        set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.NEEDS_RECONNECT)
    _setup_state(auth_client, acc)  # a Google sign-in link
    assert _mail_state(app, acc) == ServiceState.NEEDS_RECONNECT


def test_mail_setup_on_an_account_needing_reauth_asks_google(auth_client, app, make_account):
    acc = _account(app, make_account)
    with app.state.session_factory() as db:
        row = db.get(type(acc), acc.id)
        row.scopes = f"{DRIVE_SCOPE} {GMAIL_SCOPE}"
        row.status = "NEEDS_REAUTH"
        db.commit()
    _setup_state(auth_client, acc)
    assert _mail_state(app, acc) == ServiceState.NOT_SET_UP
