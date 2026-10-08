from urllib.parse import parse_qs, urlparse

import httpx
import respx
from sqlalchemy import select

from app.models import Account
from app.services.accounts.oauth import ABOUT_URL, TOKEN_URL


def _mock_google(email="alice@example.com"):
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.test",
                "refresh_token": "1//refresh-test-token-value",
                "expires_in": 3599,
                "token_type": "Bearer",
            },
        )
    )
    respx.get(ABOUT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "user": {"emailAddress": email},
                "storageQuota": {"limit": "1000", "usage": "10", "usageInDriveTrash": "1"},
            },
        )
    )


def _start(auth_client) -> str:
    url = auth_client.post("/api/accounts/connect").json()["auth_url"]
    return parse_qs(urlparse(url).query)["state"][0]


@respx.mock
def test_connect_flow_creates_account_remote_and_folder(auth_client, app, fake_rclone):
    _mock_google()
    state = _start(auth_client)
    r = auth_client.get(f"/api/oauth/callback?state={state}&code=abc", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/?connected=")
    with app.state.session_factory() as db:
        acc = db.scalar(select(Account))
    assert acc.email == "alice@example.com"
    assert acc.status == "CONNECTED"
    assert (app.state.layout.storage / "alice@example.com").is_dir()
    create = [c for c in fake_rclone.calls() if c[4:6] == ["config", "create"]][0]
    assert create[6] == acc.remote_name
    assert "use_trash" in create and "true" in create
    body = auth_client.get("/api/accounts").json()
    assert body[0]["email"] == "alice@example.com"
    assert "token" not in str(body).lower()


def test_callback_with_unknown_state_is_rejected(client):
    r = client.get("/api/oauth/callback?state=nope&code=abc", follow_redirects=False)
    assert r.status_code == 303
    assert "oauth_error=" in r.headers["location"]


def test_callback_with_google_error_is_reported(auth_client):
    state = _start(auth_client)
    r = auth_client.get(
        f"/api/oauth/callback?state={state}&error=access_denied", follow_redirects=False
    )
    assert "oauth_error=" in r.headers["location"]
    assert "access_denied" in r.headers["location"]


@respx.mock
def test_reconnect_with_different_google_account_is_refused(auth_client, app):
    _mock_google("alice@example.com")
    auth_client.get(
        f"/api/oauth/callback?state={_start(auth_client)}&code=a", follow_redirects=False
    )
    respx.reset()
    _mock_google("bob@example.com")
    url = auth_client.post("/api/accounts/1/reconnect").json()["auth_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    r = auth_client.get(f"/api/oauth/callback?state={state}&code=b", follow_redirects=False)
    assert "oauth_error=" in r.headers["location"]
    with app.state.session_factory() as db:
        assert [a.email for a in db.scalars(select(Account))] == ["alice@example.com"]


def test_connect_without_client_config_explains_what_is_missing(auth_client, app):
    app.state.settings.google_client_id = ""
    r = auth_client.post("/api/accounts/connect")
    assert r.status_code == 400
    assert "GOOGLE_CLIENT_ID" in r.json()["detail"]


def test_disconnect_keeps_local_files(auth_client, app, make_account, fake_rclone):
    acc = make_account()
    f = app.state.layout.account_dir(acc.email) / "precious.txt"
    f.write_text("keep me")
    r = auth_client.post(f"/api/accounts/{acc.id}/disconnect")
    assert r.json()["status"] == "DISCONNECTED"
    assert f.read_text() == "keep me"
    assert any(c[4:6] == ["config", "delete"] for c in fake_rclone.calls())


def test_quota_auth_failure_marks_needs_reauth(auth_client, make_account, fake_rclone):
    acc = make_account()
    # `rclone about` runs without --use-json-log, so real failures are plain text.
    fake_rclone.on(
        "about",
        stderr=[
            'CRITICAL: Failed to create file system for "gd_x:": couldn\'t fetch token: invalid_grant'
        ],
        exit=1,
    )
    r = auth_client.get(f"/api/accounts/{acc.id}/quota")
    assert r.json()["status"] == "NEEDS_REAUTH"


def test_quota_success_updates_numbers(auth_client, make_account, fake_rclone):
    acc = make_account()
    fake_rclone.on("about", stdout=['{"total": 2000, "used": 50, "trashed": 5, "free": 1950}'])
    body = auth_client.get(f"/api/accounts/{acc.id}/quota").json()
    assert (body["quota_total"], body["quota_used"], body["quota_trash"]) == (2000, 50, 5)
