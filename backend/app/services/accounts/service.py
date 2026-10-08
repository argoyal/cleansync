import json
import logging
import secrets
from collections.abc import Callable
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.clock import utcnow
from app.models import Account
from app.models.enums import AccountStatus
from app.paths import Layout, email_dirname
from app.services.accounts.oauth import (
    OAuthError,
    OAuthStates,
    build_auth_url,
    exchange_code,
    fetch_about,
    to_rclone_token,
)
from app.services.events.bus import EventBus, record_event
from app.services.rclone.commands import (
    RcloneEnv,
    about_args,
    config_create_alias_args,
    config_create_args,
    config_delete_args,
)
from app.services.rclone.errors import Failure, classify_failure
from app.services.rclone.runner import RunResult, run_rclone
from app.services.rclone.setup import ensure_config_encrypted

logger = logging.getLogger(__name__)


class AccountError(Exception):
    pass


class AccountService:
    def __init__(
        self,
        *,
        settings: Settings,
        layout: Layout,
        session_factory: sessionmaker[Session],
        bus: EventBus,
        rclone_env: RcloneEnv,
        http_factory: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=30),
        runner=run_rclone,
        clock=utcnow,
        on_change: Callable[[], None] | None = None,
        on_login_changed: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.layout = layout
        self.session_factory = session_factory
        self.bus = bus
        self.env = rclone_env
        self.http_factory = http_factory
        self.runner = runner
        self.clock = clock
        self.states = OAuthStates()
        # Called after an account's status changes, so the scheduler and watcher follow it.
        self.on_change = on_change
        # Called with the remote name whenever its saved login is replaced, removed or flagged.
        self.on_login_changed = on_login_changed

    def _login_changed(self, remote_name: str) -> None:
        if self.on_login_changed is not None:
            self.on_login_changed(remote_name)

    def _changed(self) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change()
        except Exception:  # never fail an account change over a schedule reload
            logger.exception("reloading the schedule after an account change failed")

    def _secrets(self, *extra: str) -> list[str]:
        return [self.env.config_pass, self.settings.google_client_secret, *extra]

    async def _rclone(self, args: list[str], *extra_secrets: str, on_stdout=None) -> RunResult:
        return await self.runner(
            args,
            self.env.env(),
            log_path=self.layout.logs_dir / "rclone-accounts.log",
            on_stdout=on_stdout,
            secrets=self._secrets(*extra_secrets),
        )

    def start_connect(self, reconnect_account_id: int | None = None) -> str:
        if not self.settings.google_client_id or not self.settings.google_client_secret:
            raise OAuthError(
                "Google OAuth client is not configured: set GOOGLE_CLIENT_ID and "
                "GOOGLE_CLIENT_SECRET (see docs/google-oauth-setup.md)."
            )
        state, challenge = self.states.new(reconnect_account_id)
        return build_auth_url(
            self.settings.google_client_id, self.settings.oauth_redirect_uri, state, challenge
        )

    def _new_remote_name(self, db: Session) -> str:
        while True:
            name = f"gd_{secrets.token_hex(3)}"
            if db.scalar(select(Account).where(Account.remote_name == name)) is None:
                return name

    async def finish_connect(self, state: str, code: str) -> Account:
        pending = self.states.pop(state)
        if pending is None:
            raise OAuthError("This sign-in link expired or was already used. Please try again.")
        async with self.http_factory() as http:
            tok = await exchange_code(
                http,
                client_id=self.settings.google_client_id,
                client_secret=self.settings.google_client_secret,
                redirect_uri=self.settings.oauth_redirect_uri,
                code=code,
                verifier=pending.verifier,
            )
            about = await fetch_about(http, tok["access_token"])
        email = email_dirname(about["email"])
        with self.session_factory() as db:
            if pending.reconnect_account_id is not None:
                acc = db.get(Account, pending.reconnect_account_id)
                if acc is None:
                    raise OAuthError("That account no longer exists.")
                if acc.email != email:
                    raise OAuthError(
                        "You signed in with a different Google account than the one being "
                        "reconnected."
                    )
            else:
                acc = db.scalar(select(Account).where(Account.email == email))
            name = acc.remote_name if acc else self._new_remote_name(db)
            token_json = to_rclone_token(tok, self.clock())
            # `config create` replaces an existing section of the same name, so a reconnect
            # needs no delete first (a delete-then-failed-create would lose the old login).
            res = await self._rclone(
                config_create_args(
                    self.env,
                    name,
                    self.settings.google_client_id,
                    self.settings.google_client_secret,
                    token_json,
                ),
                tok["access_token"],
                tok["refresh_token"],
            )
            if res.exit_code != 0:
                if acc is not None:
                    db.rollback()
                    await self.mark_needs_reauth(acc.id, "saving the new Google login failed")
                raise OAuthError(
                    "Could not save the Google login (rclone config create failed). See the logs."
                )
            try:
                await ensure_config_encrypted(self.env, self.runner)
            except Exception as e:
                logger.error("rclone config encryption failed: %s", e)
                await self._rclone(config_delete_args(self.env, name))
                if acc is not None:
                    db.rollback()
                    await self.mark_needs_reauth(acc.id, "saving the new Google login failed")
                raise OAuthError(
                    "Could not encrypt the saved login, so it was not kept. See the logs."
                ) from e
            if acc is None:
                acc = Account(email=email, remote_name=name)
                db.add(acc)
            self._login_changed(name)
            acc.status = AccountStatus.CONNECTED
            acc.quota_total = about["quota_total"]
            acc.quota_used = about["quota_used"]
            acc.quota_trash = about["quota_trash"]
            acc.connected_at = self.clock()
            acc.last_checked_at = self.clock()
            db.commit()
            self.layout.account_dir(email).mkdir(parents=True, exist_ok=True)
            record_event(
                db,
                self.bus,
                kind="account_connected",
                message=f"Connected Google account {email}",
                account_id=acc.id,
            )
            self.bus.publish("account", {"account_id": acc.id, "status": acc.status})
            self._changed()
            return acc

    async def add_local_account(self, email: str, folder: Path) -> Account:
        """Dev/CI only: a pretend Google account whose Drive is a local folder (alias remote).

        Refused unless DRIVESYNC_ALLOW_LOCAL_ACCOUNTS is on. `folder` must come from the
        operator's command line or app-owned paths, never from a web request.
        """
        if not self.settings.allow_local_accounts:
            raise AccountError(
                "Local pretend accounts are for development only. "
                "Set DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 to allow one."
            )
        email = email_dirname(email)
        folder = folder.resolve()
        with self.session_factory() as db:
            if db.scalar(select(Account).where(Account.email == email)) is not None:
                raise AccountError(f"{email} is already connected")
            name = f"local_{secrets.token_hex(3)}"
            while db.scalar(select(Account).where(Account.remote_name == name)) is not None:
                name = f"local_{secrets.token_hex(3)}"
            folder.mkdir(parents=True, exist_ok=True)
            res = await self._rclone(config_create_alias_args(self.env, name, folder))
            if res.exit_code != 0:
                raise AccountError("rclone could not save the remote: " + "; ".join(res.error_tail))
            await ensure_config_encrypted(self.env, self.runner)
            acc = Account(
                email=email,
                remote_name=name,
                status=AccountStatus.CONNECTED,
                connected_at=self.clock(),
                last_checked_at=self.clock(),
            )
            db.add(acc)
            self._login_changed(name)
            db.commit()
            self.layout.account_dir(email).mkdir(parents=True, exist_ok=True)
            logger.warning(
                "added PRETEND local account %s (folder %s); development only", email, folder
            )
            record_event(
                db,
                self.bus,
                kind="account_connected",
                message=f"Added pretend local account {email}",
                account_id=acc.id,
            )
            self.bus.publish("account", {"account_id": acc.id, "status": acc.status})
            self._changed()
            return acc

    async def disconnect(self, account_id: int) -> Account:
        with self.session_factory() as db:
            acc = db.get(Account, account_id)
            if acc is None:
                raise LookupError("account not found")
            res = await self._rclone(config_delete_args(self.env, acc.remote_name))
            if res.exit_code != 0:
                raise AccountError("Could not remove the saved Google login; see the logs.")
            self._login_changed(acc.remote_name)
            acc.status = AccountStatus.DISCONNECTED
            db.commit()
            record_event(
                db,
                self.bus,
                kind="account_disconnected",
                message=f"Disconnected {acc.email}. Local files were kept.",
                account_id=acc.id,
            )
            self.bus.publish("account", {"account_id": acc.id, "status": acc.status})
            self._changed()
            return acc

    async def mark_needs_reauth(self, account_id: int, reason: str) -> None:
        with self.session_factory() as db:
            acc = db.get(Account, account_id)
            if acc is None:
                return
            self._login_changed(acc.remote_name)
            if acc.status == AccountStatus.NEEDS_REAUTH:
                return
            acc.status = AccountStatus.NEEDS_REAUTH
            db.commit()
            record_event(
                db,
                self.bus,
                kind="account_needs_reauth",
                level="warning",
                message=f"Google login for {acc.email} needs attention: {reason}",
                account_id=acc.id,
            )
            self.bus.publish("account", {"account_id": acc.id, "status": acc.status})
        self._changed()

    async def refresh_quota(self, account_id: int) -> Account:
        out: list[str] = []
        with self.session_factory() as db:
            acc = db.get(Account, account_id)
            if acc is None:
                raise LookupError("account not found")
            remote = acc.remote_name
        res = await self._rclone(about_args(self.env, remote), on_stdout=out.append)
        failure = classify_failure(res.exit_code, res.error_tail)
        if failure == Failure.AUTH:
            await self.mark_needs_reauth(account_id, "Google says the login expired or was revoked")
        with self.session_factory() as db:
            acc = db.get(Account, account_id)
            if failure is None:
                data = json.loads("\n".join(out) or "{}")
                acc.quota_total = data.get("total")
                acc.quota_used = data.get("used")
                acc.quota_trash = data.get("trashed")
                acc.last_checked_at = self.clock()
                db.commit()
            return acc
