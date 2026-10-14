import base64
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ABOUT_URL = "https://www.googleapis.com/drive/v3/about"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
# Read, label and trash mail; Plan 4's restore imports and "empty trash" deletes with it too.
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
# Read, send and permanently delete all mail (bulk offload spec §3). Asked for only when
# the owner turns on Allow permanent delete for a mailbox.
FULL_MAIL_SCOPE = "https://mail.google.com/"
# Create, list and delete Gmail filters (0.5.2 spec §5). Asked for only when the owner turns
# on Allow Gmail filters for a mailbox.
SETTINGS_SCOPE = "https://www.googleapis.com/auth/gmail.settings.basic"
SCOPES = f"{DRIVE_SCOPE} {GMAIL_SCOPE}"
PERMANENT_PURPOSE = "permanent_delete"  # a sign-in for Turn on (bulk offload spec §3)
FILTERS_PURPOSE = "gmail_filters"  # a sign-in for Turn on of Allow Gmail filters


def granted_scopes(token_answer: dict) -> str:
    """The permissions Google granted, from its token answer. Without a "scope" field, only
    Drive is assumed: Mail must never be thought to work when it might not."""
    raw = token_answer.get("scope")
    if not isinstance(raw, str) or not raw.strip():
        return DRIVE_SCOPE
    return " ".join(sorted(set(raw.split())))


def has_scope(scopes: str, scope: str) -> bool:
    return scope in scopes.split()


class OAuthError(Exception):
    pass


@dataclass
class PendingAuth:
    verifier: str
    created: float
    reconnect_account_id: int | None
    purpose: str = "connect"  # "connect", "mail", "permanent_delete" or "gmail_filters"


class OAuthStates:
    def __init__(self, ttl_s: float = 600, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self.clock = clock
        self._pending: dict[str, PendingAuth] = {}

    def new(
        self, reconnect_account_id: int | None = None, purpose: str = "connect"
    ) -> tuple[str, str]:
        now = self.clock()
        self._pending = {k: v for k, v in self._pending.items() if now - v.created < self.ttl_s}
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self._pending[state] = PendingAuth(verifier, now, reconnect_account_id, purpose)
        return state, challenge

    def peek(self, state: str) -> PendingAuth | None:
        """What a sign-in was for, without using it up."""
        pending = self._pending.get(state)
        if pending is None or self.clock() - pending.created >= self.ttl_s:
            return None
        return pending

    def forget(self, account_id: int, purpose: str) -> None:
        """Drop the sign-ins still waiting for this account and purpose (Turn off: an old
        Google tab must not turn the switch back on)."""
        self._pending = {
            k: v
            for k, v in self._pending.items()
            if not (v.reconnect_account_id == account_id and v.purpose == purpose)
        }

    def pop(self, state: str) -> PendingAuth | None:
        pending = self._pending.pop(state, None)
        if pending is None or self.clock() - pending.created >= self.ttl_s:
            return None
        return pending


def build_auth_url(
    client_id: str, redirect_uri: str, state: str, challenge: str, scope: str = SCOPES
) -> str:
    return (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": scope,
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )


async def exchange_code(
    http: httpx.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    verifier: str,
) -> dict:
    resp = await http.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        },
    )
    if resp.status_code != 200:
        try:
            err = resp.json().get("error", "unknown_error")
        except ValueError:
            err = f"http_{resp.status_code}"
        raise OAuthError(f"Google refused the sign-in ({err}).")
    data = resp.json()
    if not data.get("refresh_token"):
        raise OAuthError(
            "Google did not return a refresh token. Remove CleanSync's access at "
            "myaccount.google.com/permissions and connect again."
        )
    return data


def to_rclone_token(resp: dict, now: datetime) -> str:
    expiry = now + timedelta(seconds=int(resp.get("expires_in", 3600)))
    return json.dumps(
        {
            "access_token": resp["access_token"],
            "token_type": resp.get("token_type", "Bearer"),
            "refresh_token": resp["refresh_token"],
            "expiry": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )


async def fetch_about(http: httpx.AsyncClient, access_token: str) -> dict:
    resp = await http.get(
        ABOUT_URL,
        params={"fields": "user(emailAddress),storageQuota"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code != 200:
        raise OAuthError(f"Could not read the Google account details (HTTP {resp.status_code}).")
    data = resp.json()
    quota = data.get("storageQuota", {})
    return {
        "email": data["user"]["emailAddress"],
        "quota_total": int(quota["limit"]) if quota.get("limit") else None,
        "quota_used": int(quota.get("usage", 0)),
        "quota_trash": int(quota.get("usageInDriveTrash", 0)),
    }
