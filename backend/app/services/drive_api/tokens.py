"""Short-lived Google access tokens for the few Drive API calls rclone can't make.

The refresh token stays where rclone keeps it (the encrypted rclone.conf). We read it with
`rclone config dump`, swap it for an access token at Google, keep that in memory only, and
never log either one. Error messages here never carry any text from the dump or from Google.
"""

import asyncio
import json
import time
from collections.abc import Callable

import httpx

from app.config import Settings
from app.services.accounts.oauth import TOKEN_URL
from app.services.rclone.commands import RcloneEnv, config_dump_args
from app.services.rclone.runner import run_rclone

DUMP_TIMEOUT_S = 30
EARLY_S = 120  # refresh this long before Google's expiry


class DriveApiError(Exception):
    pass


class DriveNotFoundError(DriveApiError):
    """Google says the target folder does not exist (any more)."""


class DriveForbiddenError(DriveApiError):
    """Google says this account may not add files to the target folder."""


class DriveAuthError(DriveApiError):
    """Google refused the saved login: the account must be reconnected."""


# Every failure below is raised outside any `except` block, with nothing chained: a chained
# exception (or its __context__) could carry the dump text or an httpx request that holds
# the refresh token, the client secret or the Authorization header.


def parse_refresh_token(dump: str, remote_name: str) -> str | None:
    try:
        section = json.loads(dump)[remote_name]
        return str(json.loads(section["token"])["refresh_token"])
    except (ValueError, KeyError, TypeError):
        return None


def parse_access_token(text_json: object) -> tuple[str, float] | None:
    try:
        return str(text_json["access_token"]), float(text_json.get("expires_in", 3600))  # type: ignore[index, attr-defined]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def error_code(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("error", ""))
    except (ValueError, AttributeError):
        return ""


def json_or_none(resp: httpx.Response) -> object:
    try:
        return resp.json()
    except ValueError:
        return None


async def post_or_none(
    http_factory: Callable[[], httpx.AsyncClient], url: str, **kwargs
) -> httpx.Response | None:
    """The response, or None when the request itself failed (the httpx error is dropped)."""
    try:
        async with http_factory() as http:
            return await http.post(url, **kwargs)
    except httpx.HTTPError:
        return None


class TokenProvider:
    def __init__(
        self,
        *,
        settings: Settings,
        rclone_env: RcloneEnv,
        runner=run_rclone,
        http_factory: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=30),
        clock: Callable[[], float] = time.monotonic,
        dump_timeout: float = DUMP_TIMEOUT_S,
    ) -> None:
        self.settings = settings
        self.env = rclone_env
        self.runner = runner
        self.http_factory = http_factory
        self.clock = clock
        self.dump_timeout = dump_timeout
        self._cache: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def forget(self, remote_name: str) -> None:
        self._cache.pop(remote_name, None)

    def _cached(self, remote_name: str) -> str | None:
        hit = self._cache.get(remote_name)
        if hit is not None and hit[1] - EARLY_S > self.clock():
            return hit[0]
        return None

    async def access_token(self, remote_name: str) -> str:
        token = self._cached(remote_name)
        if token is not None:
            return token
        lock = self._locks.setdefault(remote_name, asyncio.Lock())
        async with lock:
            token = self._cached(remote_name)
            if token is not None:
                return token
            refresh = await self._refresh_token(remote_name)
            token, ttl = await self._exchange(refresh)
            self._cache[remote_name] = (token, self.clock() + ttl)
            return token

    async def _refresh_token(self, remote_name: str) -> str:
        out: list[str] = []
        timed_out = False
        res = None
        try:
            res = await asyncio.wait_for(
                self.runner(
                    config_dump_args(self.env),
                    self.env.env(),
                    log_path=None,  # stdout holds every saved login: never written anywhere
                    on_stdout=out.append,
                    secrets=[self.env.config_pass],
                ),
                self.dump_timeout,
            )
        except TimeoutError:
            timed_out = True  # the runner's cancel path kills the process
        refresh = None
        if res is not None and res.exit_code == 0:
            refresh = parse_refresh_token("\n".join(out), remote_name)
        out.clear()
        if timed_out:
            raise DriveApiError("reading the saved Google login took too long")
        if res is None or res.exit_code != 0:
            raise DriveApiError("could not read the saved Google login")
        if refresh is None:
            raise DriveApiError("the saved Google login is incomplete; reconnect the account")
        return refresh

    async def _exchange(self, refresh_token: str) -> tuple[str, float]:
        resp = await post_or_none(
            self.http_factory,
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
            },
        )
        if resp is None:
            raise DriveApiError("could not reach Google to sign in")
        if resp.status_code != 200:
            if error_code(resp) in ("invalid_grant", "unauthorized_client", "invalid_client"):
                raise DriveAuthError("Google says the login expired or was revoked")
            raise DriveApiError(f"Google refused the token request (HTTP {resp.status_code})")
        parsed = parse_access_token(json_or_none(resp))
        if parsed is None:
            raise DriveApiError("Google gave an unexpected answer to the token request")
        return parsed
