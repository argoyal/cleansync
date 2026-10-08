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
    assert "cancelled" in r.headers["location"]


def test_callback_google_error_never_echoes_query_value(auth_client):
    state = _start(auth_client)
    r = auth_client.get(
        f"/api/oauth/callback?state={state}&error=<script>evil", follow_redirects=False
    )
    assert "evil" not in r.headers["location"]
    # the state was consumed
    r2 = auth_client.get(f"/api/oauth/callback?state={state}&code=x", follow_redirects=False)
    assert "oauth_error=" in r2.headers["location"]


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


def test_quota_auth_failure_json_log_line_marks_needs_reauth(
    auth_client, make_account, fake_rclone
):
    acc = make_account()
    fake_rclone.on(
        "about", stderr=['{"level":"error","msg":"couldn\'t fetch token: invalid_grant"}'], exit=1
    )
    r = auth_client.get(f"/api/accounts/{acc.id}/quota")
    assert r.json()["status"] == "NEEDS_REAUTH"


def _connect(auth_client) -> None:
    _mock_google()
    auth_client.get(
        f"/api/oauth/callback?state={_start(auth_client)}&code=a", follow_redirects=False
    )


@respx.mock
def test_reconnect_does_not_delete_first(auth_client, fake_rclone):
    _connect(auth_client)
    respx.reset()
    _mock_google()
    before = len(fake_rclone.calls())
    url = auth_client.post("/api/accounts/1/reconnect").json()["auth_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    auth_client.get(f"/api/oauth/callback?state={state}&code=b", follow_redirects=False)
    new = fake_rclone.calls()[before:]
    assert not any(c[4:6] == ["config", "delete"] for c in new)
    assert any(c[4:6] == ["config", "create"] for c in new)


@respx.mock
def test_failed_create_on_reconnect_marks_needs_reauth(auth_client, fake_rclone):
    _connect(auth_client)
    respx.reset()
    _mock_google()
    fake_rclone.on("config", match=["create"], exit=1)
    url = auth_client.post("/api/accounts/1/reconnect").json()["auth_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    r = auth_client.get(f"/api/oauth/callback?state={state}&code=b", follow_redirects=False)
    assert "oauth_error=" in r.headers["location"]
    assert auth_client.get("/api/accounts").json()[0]["status"] == "NEEDS_REAUTH"


def test_disconnect_failure_keeps_status_and_returns_502(auth_client, make_account, fake_rclone):
    acc = make_account()
    fake_rclone.on("config", match=["delete"], exit=1)
    r = auth_client.post(f"/api/accounts/{acc.id}/disconnect")
    assert r.status_code == 502
    assert "could not remove the saved google login" in r.json()["detail"].lower()
    assert auth_client.get("/api/accounts").json()[0]["status"] == "CONNECTED"


@respx.mock
def test_encryption_failure_removes_remote_and_refuses(auth_client, app, fake_rclone):
    _mock_google()
    fake_rclone.on("config", match=["encryption"], exit=1)
    app.state.layout.rclone_conf.write_text("[x]\ntype = drive\n")
    state = _start(auth_client)
    r = auth_client.get(f"/api/oauth/callback?state={state}&code=a", follow_redirects=False)
    assert "encrypt" in r.headers["location"]
    assert any(c[4:6] == ["config", "delete"] for c in fake_rclone.calls())
    assert auth_client.get("/api/accounts").json() == []


def test_unexpected_error_in_callback_is_generic(auth_client, app):
    async def boom(state, code):
        raise RuntimeError("secret detail user@example.com")

    app.state.accounts.finish_connect = boom
    r = auth_client.get("/api/oauth/callback?state=s&code=c", follow_redirects=False)
    assert r.status_code == 303
    assert "see%20the%20logs" in r.headers["location"]
    assert "secret" not in r.headers["location"]


def test_startup_encrypts_existing_plain_config(app, fake_rclone):
    from fastapi.testclient import TestClient

    app.state.layout.rclone_conf.write_text("[x]\ntype = drive\n")
    with TestClient(app):
        pass
    assert any(c[4:7] == ["config", "encryption", "set"] for c in fake_rclone.calls())


# I2: the scheduler and watcher must follow account status changes.
@respx.mock
def test_reconnect_resumes_scheduling(auth_client, app, make_account, make_pair, db):
    acc = make_account()
    acc.status = "NEEDS_REAUTH"
    db.commit()
    pair = make_pair(acc, initialized=True, schedule_interval_s=300)
    sched = app.state.scheduler
    sched.reload()
    assert sched.aps.get_job(f"pair-{pair.id}") is None
    _mock_google()
    url = auth_client.post(f"/api/accounts/{acc.id}/reconnect").json()["auth_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    r = auth_client.get(f"/api/oauth/callback?state={state}&code=a", follow_redirects=False)
    assert r.headers["location"].startswith("/?connected=")
    assert sched.aps.get_job(f"pair-{pair.id}") is not None


def test_disconnect_and_needs_reauth_stop_scheduling(auth_client, app, make_account, make_pair):
    acc = make_account()
    p1 = make_pair(acc, initialized=True, schedule_interval_s=300)
    other = make_account("bob@example.com")
    p2 = make_pair(other, initialized=True, schedule_interval_s=300)
    sched = app.state.scheduler
    sched.reload()
    assert sched.aps.get_job(f"pair-{p1.id}") and sched.aps.get_job(f"pair-{p2.id}")
    auth_client.post(f"/api/accounts/{acc.id}/disconnect")
    assert sched.aps.get_job(f"pair-{p1.id}") is None

    async def expire():
        await app.state.accounts.mark_needs_reauth(other.id, "expired")

    auth_client.portal.call(expire)
    assert sched.aps.get_job(f"pair-{p2.id}") is None
