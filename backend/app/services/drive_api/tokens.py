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

EARLY_S = 120  # refresh this long before Google's expiry


class DriveApiError(Exception):
    pass


class DriveAuthError(DriveApiError):
    """Google refused the saved login: the account must be reconnected."""


class TokenProvider:
    def __init__(
        self,
        *,
        settings: Settings,
        rclone_env: RcloneEnv,
        runner=run_rclone,
        http_factory: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=30),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.env = rclone_env
        self.runner = runner
        self.http_factory = http_factory
        self.clock = clock
        self._cache: dict[str, tuple[str, float]] = {}
        self._lock: asyncio.Lock | None = None

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
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            token = self._cached(remote_name)
            if token is not None:
                return token
            refresh = await self._refresh_token(remote_name)
            token, ttl = await self._exchange(refresh)
            self._cache[remote_name] = (token, self.clock() + ttl)
            return token

    async def _refresh_token(self, remote_name: str) -> str:
        out: list[str] = []
        res = await self.runner(
            config_dump_args(self.env),
            self.env.env(),
            log_path=None,  # stdout holds every saved login: never written anywhere
            on_stdout=out.append,
            secrets=[self.env.config_pass],
        )
        try:
            if res.exit_code != 0:
                raise DriveApiError("could not read the saved Google login")
            try:
                section = json.loads("\n".join(out))[remote_name]
                return str(json.loads(section["token"])["refresh_token"])
            except (ValueError, KeyError, TypeError):
                # No details: the text around the error could be part of a token.
                raise DriveApiError(
                    "the saved Google login is incomplete; reconnect the account"
                ) from None
        finally:
            out.clear()

    async def _exchange(self, refresh_token: str) -> tuple[str, float]:
        try:
            async with self.http_factory() as http:
                resp = await http.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": self.settings.google_client_id,
                        "client_secret": self.settings.google_client_secret,
                    },
                )
        except httpx.HTTPError:
            raise DriveApiError("could not reach Google to sign in") from None
        if resp.status_code != 200:
            try:
                err = str(resp.json().get("error", ""))
            except (ValueError, AttributeError):
                err = ""
            if err in ("invalid_grant", "unauthorized_client", "invalid_client"):
                raise DriveAuthError("Google says the login expired or was revoked")
            raise DriveApiError(f"Google refused the token request (HTTP {resp.status_code})")
        try:
            data = resp.json()
            return str(data["access_token"]), float(data.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError, AttributeError):
            raise DriveApiError("Google gave an unexpected answer to the token request") from None
