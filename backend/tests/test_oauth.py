import base64
import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.services.accounts.oauth import (
    ABOUT_URL,
    TOKEN_URL,
    OAuthError,
    OAuthStates,
    build_auth_url,
    exchange_code,
    fetch_about,
    to_rclone_token,
)


def test_state_is_single_use_and_expires():
    now = [0.0]
    states = OAuthStates(ttl_s=600, clock=lambda: now[0])
    s1, _ = states.new()
    assert states.pop(s1) is not None
    assert states.pop(s1) is None
    s2, _ = states.new(reconnect_account_id=7)
    now[0] = 601
    assert states.pop(s2) is None


def test_pkce_challenge_matches_verifier():
    states = OAuthStates()
    state, challenge = states.new()
    pending = states.pop(state)
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(pending.verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == expected


def test_auth_url_asks_for_drive_and_gmail_offline():
    url = build_auth_url("cid", "https://x.example.com/api/oauth/callback", "st", "ch")
    q = parse_qs(urlparse(url).query)
    assert q["scope"] == [
        "https://www.googleapis.com/auth/drive https://www.googleapis.com/auth/gmail.modify"
    ]
    assert q["include_granted_scopes"] == ["true"]
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["st"]


def test_granted_scopes_reads_googles_answer_and_assumes_drive_only_without_it():
    from app.services.accounts.oauth import DRIVE_SCOPE, GMAIL_SCOPE, granted_scopes, has_scope

    both = granted_scopes({"scope": f"{GMAIL_SCOPE} {DRIVE_SCOPE} openid"})
    assert has_scope(both, GMAIL_SCOPE) and has_scope(both, DRIVE_SCOPE)
    assert granted_scopes({}) == DRIVE_SCOPE
    assert not has_scope(granted_scopes({}), GMAIL_SCOPE)
    assert not has_scope("", GMAIL_SCOPE)


def test_peek_does_not_use_up_the_state():
    states = OAuthStates()
    s, _ = states.new(reconnect_account_id=3, purpose="mail")
    assert states.peek(s).purpose == "mail"
    assert states.pop(s).reconnect_account_id == 3
    assert states.peek(s) is None


@respx.mock
async def test_exchange_requires_refresh_token():
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "a", "expires_in": 3599})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(OAuthError, match="refresh token"):
            await exchange_code(
                http, client_id="c", client_secret="s", redirect_uri="r", code="code", verifier="v"
            )


@respx.mock
async def test_exchange_error_is_reported_without_secret():
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(OAuthError, match="invalid_grant") as exc:
            await exchange_code(
                http,
                client_id="c",
                client_secret="s3cr3t",
                redirect_uri="r",
                code="code",
                verifier="v",
            )
    assert "s3cr3t" not in str(exc.value)


def test_rclone_token_format():
    tok = json.loads(
        to_rclone_token(
            {"access_token": "a", "refresh_token": "r", "token_type": "Bearer", "expires_in": 3600},
            datetime(2026, 10, 8, 8, 0, tzinfo=UTC),
        )
    )
    assert tok == {
        "access_token": "a",
        "token_type": "Bearer",
        "refresh_token": "r",
        "expiry": "2026-10-08T09:00:00Z",
    }


@respx.mock
async def test_fetch_about_reads_email_and_quota():
    respx.get(ABOUT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "user": {"emailAddress": "Alice@Example.com"},
                "storageQuota": {"limit": "16106127360", "usage": "100", "usageInDriveTrash": "7"},
            },
        )
    )
    async with httpx.AsyncClient() as http:
        about = await fetch_about(http, "a")
    assert about == {
        "email": "Alice@Example.com",
        "quota_total": 16106127360,
        "quota_used": 100,
        "quota_trash": 7,
    }
