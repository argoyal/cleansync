import json
import logging
import secrets
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.clock import as_utc, utcnow
from app.models import Account
from app.models.enums import AccountStatus, ServiceKind, ServiceState
from app.paths import Layout, email_dirname
from app.services.accounts.filters import permission_lost as filters_permission_lost
from app.services.accounts.oauth import (
    DRIVE_SCOPE,
    FILTERS_PURPOSE,
    FULL_MAIL_SCOPE,
    GMAIL_SCOPE,
    PERMANENT_PURPOSE,
    SCOPES,
    SETTINGS_SCOPE,
    OAuthError,
    OAuthStates,
    build_auth_url,
    exchange_code,
    fetch_about,
    granted_scopes,
    has_scope,
    to_rclone_token,
)
from app.services.accounts.permanent import permission_lost
from app.services.accounts.photos import photo_http_factory, save_photo
from app.services.accounts.services import (
    ensure_services,
    get_service,
    service_state,
    set_service_state,
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


PRETEND_PREFIX = "local_"
MAIL_LOST_NOTE = "Google no longer lets CleanSync read this account's mail. Set up mail again."


def is_pretend(account: Account, allowed: bool) -> bool:
    """A pretend local account (development and CI only): no Google behind it, no Drive IDs,
    no trash. Only with DRIVESYNC_ALLOW_LOCAL_ACCOUNTS on (`allowed`)."""
    return allowed and account.remote_name.startswith(PRETEND_PREFIX)


def check_local_folder(layout: Layout, env: RcloneEnv, folder: Path) -> Path:
    """Resolve a pretend-account folder and refuse anything that touches the app's own folders.

    Nothing is created. Also runs the rclone argument guard (control characters etc.).
    """
    folder = folder.resolve()
    own = [layout.root.resolve(), (layout.root / "app").resolve(), layout.data.resolve()]
    own.append(layout.storage.resolve())
    own.append(layout.mail_root.resolve())
    for p in own:
        if folder == p or (folder.is_relative_to(p) and p != layout.root.resolve()):
            raise AccountError("That folder is inside the app's own folders; pick another one.")
        if p.is_relative_to(folder):
            raise AccountError("That folder contains the app's own folders; pick another one.")
    config_create_alias_args(env, "local_check", folder)  # raises UnsafeArgument if unsafe
    return folder


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
        self.photo_http_factory = photo_http_factory
        # Set in main.py once the Drive client exists; used to look up the profile photo.
        self.drive_api = None
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

    def start_connect(
        self, reconnect_account_id: int | None = None, purpose: str = "connect"
    ) -> str:
        if not self.settings.google_client_id or not self.settings.google_client_secret:
            raise OAuthError(
                "Google OAuth client is not configured: set GOOGLE_CLIENT_ID and "
                "GOOGLE_CLIENT_SECRET (see docs/google-oauth-setup.md)."
            )
        state, challenge = self.states.new(reconnect_account_id, purpose)
        scope = SCOPES
        if purpose == PERMANENT_PURPOSE:
            scope = f"{SCOPES} {FULL_MAIL_SCOPE}"
        elif purpose == FILTERS_PURPOSE:
            scope = f"{SCOPES} {SETTINGS_SCOPE}"
        return build_auth_url(
            self.settings.google_client_id,
            self.settings.oauth_redirect_uri,
            state,
            challenge,
            scope,
        )

    def start_mail_setup(self, account_id: int) -> str | None:
        """Ask Google again, now with Gmail too (spec §3.1). Drive keeps syncing meanwhile.

        A pretend local account has no Google behind it: it goes straight to "setting up"
        (Plan 2 gives it a fake Gmail) and None is returned. So does a connected account
        whose sign-in already allows Gmail, unless Mail is waiting to be reconnected (Gmail
        refused the saved permission, so only Google can say whether it is back)."""
        with self.session_factory() as db:
            acc = db.get(Account, account_id)
            if acc is None:
                raise LookupError("account not found")
            if acc.status == AccountStatus.DISCONNECTED:
                raise AccountError("Connect this Google account again first (Settings, Accounts).")
            if is_pretend(acc, self.settings.allow_local_accounts):
                row = get_service(db, acc.id, ServiceKind.MAIL)
                if row is None or row.state == ServiceState.NOT_SET_UP:
                    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.SETTING_UP)
                return None
            row = get_service(db, acc.id, ServiceKind.MAIL)
            if (
                has_scope(acc.scopes, GMAIL_SCOPE)
                and acc.status == AccountStatus.CONNECTED
                and (row is None or row.state != ServiceState.NEEDS_RECONNECT)
            ):
                # Google already allows Gmail for this account: no need to ask again.
                self._follow_mail_permission(db, acc, "mail")
                return None
        return self.start_connect(account_id, purpose="mail")

    def start_permanent_delete(self, account_id: int) -> str | None:
        """Turn on Allow permanent delete (bulk offload spec §3). Google is asked for the full
        Gmail permission only when it hasn't granted it yet; a pretend local account gets it
        without Google. None: it is on now."""
        with self.session_factory() as db:
            acc = db.get(Account, account_id)
            if acc is None:
                raise LookupError("account not found")
            if acc.status != AccountStatus.CONNECTED:
                raise AccountError("Connect this Google account again first (Settings, Accounts).")
            if service_state(db, acc.id, ServiceKind.MAIL) == ServiceState.NOT_SET_UP:
                raise AccountError("Set up mail backup for this account first.")
            if is_pretend(acc, self.settings.allow_local_accounts):
                acc.scopes = " ".join(sorted(set(acc.scopes.split()) | {FULL_MAIL_SCOPE}))
            if has_scope(acc.scopes, FULL_MAIL_SCOPE):
                acc.permanent_delete, acc.permanent_delete_note = True, None
                db.commit()
                self.bus.publish("account", {"account_id": acc.id, "status": acc.status})
                return None
        return self.start_connect(account_id, purpose=PERMANENT_PURPOSE)

    def _follow_permanent_delete(self, db: Session, acc: Account, purpose: str) -> None:
        """After any sign-in: Turn on's sign-in that granted the scope turns the switch on;
        a sign-in without it, while the switch was on, means Google took it back. Any other
        sign-in never turns it on."""
        if has_scope(acc.scopes, FULL_MAIL_SCOPE):
            if purpose == PERMANENT_PURPOSE:
                acc.permanent_delete, acc.permanent_delete_note = True, None
                db.commit()
        elif acc.permanent_delete:
            permission_lost(db, self.bus, acc.id)

    def start_gmail_filters(self, account_id: int) -> str | None:
        """Turn on Allow Gmail filters (0.5.2 spec §5). Google is asked for the Gmail settings
        permission only when it hasn't granted it yet; a pretend local account gets it
        without Google. None: it is on now."""
        with self.session_factory() as db:
            acc = db.get(Account, account_id)
            if acc is None:
                raise LookupError("account not found")
            if acc.status != AccountStatus.CONNECTED:
                raise AccountError("Connect this Google account again first (Settings, Accounts).")
            if service_state(db, acc.id, ServiceKind.MAIL) == ServiceState.NOT_SET_UP:
                raise AccountError("Set up mail backup for this account first.")
            if is_pretend(acc, self.settings.allow_local_accounts):
                acc.scopes = " ".join(sorted(set(acc.scopes.split()) | {SETTINGS_SCOPE}))
            if has_scope(acc.scopes, SETTINGS_SCOPE):
                acc.gmail_filters, acc.gmail_filters_note = True, None
                db.commit()
                self.bus.publish("account", {"account_id": acc.id, "status": acc.status})
                return None
        return self.start_connect(account_id, purpose=FILTERS_PURPOSE)

    def _follow_gmail_filters(self, db: Session, acc: Account, purpose: str) -> None:
        """After any sign-in: Turn on's sign-in that granted the scope turns the switch on;
        a sign-in without it, while the switch was on, means Google took it back. Any other
        sign-in never turns it on."""
        if has_scope(acc.scopes, SETTINGS_SCOPE):
            if purpose == FILTERS_PURPOSE:
                acc.gmail_filters, acc.gmail_filters_note = True, None
                db.commit()
        elif acc.gmail_filters:
            filters_permission_lost(db, self.bus, acc.id)

    def _follow_mail_permission(self, db: Session, acc: Account, purpose: str) -> None:
        """Mail's state after a sign-in, from what Google granted (see the plan's rulings)."""
        row = get_service(db, acc.id, ServiceKind.MAIL)
        state = row.state if row is not None else ServiceState.NOT_SET_UP
        if has_scope(acc.scopes, GMAIL_SCOPE):
            if state == ServiceState.NEEDS_RECONNECT:
                set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
            elif state == ServiceState.NOT_SET_UP and purpose == "mail":
                set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.SETTING_UP)
        elif state in (ServiceState.SETTING_UP, ServiceState.ACTIVE, ServiceState.PAUSED):
            set_service_state(
                db, acc.id, ServiceKind.MAIL, ServiceState.NEEDS_RECONNECT, MAIL_LOST_NOTE
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
        scopes = granted_scopes(tok)
        if not has_scope(scopes, DRIVE_SCOPE):
            raise OAuthError(
                "Google did not allow Drive access, so nothing was changed. "
                "Please sign in again and keep Drive ticked."
            )
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
            acc.scopes = scopes
            acc.quota_total = about["quota_total"]
            acc.quota_used = about["quota_used"]
            acc.quota_trash = about["quota_trash"]
            acc.connected_at = self.clock()
            acc.last_checked_at = self.clock()
            db.commit()
            ensure_services(db, acc)
            self._follow_mail_permission(db, acc, pending.purpose)
            self._follow_permanent_delete(db, acc, pending.purpose)
            self._follow_gmail_filters(db, acc, pending.purpose)
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
            await save_photo(
                self.layout,
                self.session_factory,
                acc.id,
                email,
                about.get("photo_link"),
                self.photo_http_factory,
                self.clock,
            )
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
        folder = check_local_folder(self.layout, self.env, folder)
        with self.session_factory() as db:
            if db.scalar(select(Account).where(Account.email == email)) is not None:
                raise AccountError(f"{email} is already connected")
            name = f"{PRETEND_PREFIX}{secrets.token_hex(3)}"
            while db.scalar(select(Account).where(Account.remote_name == name)) is not None:
                name = f"{PRETEND_PREFIX}{secrets.token_hex(3)}"
            folder.mkdir(parents=True, exist_ok=True)
            res = await self._rclone(config_create_alias_args(self.env, name, folder))
            if res.exit_code != 0:
                raise AccountError("rclone could not save the remote: " + "; ".join(res.error_tail))
            await ensure_config_encrypted(self.env, self.runner)
            acc = Account(
                email=email,
                remote_name=name,
                status=AccountStatus.CONNECTED,
                scopes=SCOPES,
                connected_at=self.clock(),
                last_checked_at=self.clock(),
            )
            db.add(acc)
            self._login_changed(name)
            db.commit()
            ensure_services(db, acc)
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
            if acc.status == AccountStatus.DISCONNECTED:
                return  # disconnected on purpose: a late failure must not undo that
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

    async def storage_outside_drive(self, account_id: int) -> int | None:
        """What Google storage uses outside Drive (Gmail plus Google Photos; Google does not
        report Gmail on its own), from `rclone about`. None when unknown."""
        out: list[str] = []
        with self.session_factory() as db:
            acc = db.get(Account, account_id)
            if acc is None:
                return None
            remote = acc.remote_name
        res = await self._rclone(about_args(self.env, remote), on_stdout=out.append)
        if res.exit_code != 0:
            return None
        try:
            other = json.loads("\n".join(out) or "{}").get("other")
        except ValueError:
            return None
        return int(other) if isinstance(other, int) else None

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
        photo_due, email = False, ""
        with self.session_factory() as db:
            acc = db.get(Account, account_id)
            if failure is None:
                data = json.loads("\n".join(out) or "{}")
                acc.quota_total = data.get("total")
                acc.quota_used = data.get("used")
                acc.quota_trash = data.get("trashed")
                acc.last_checked_at = self.clock()
                db.commit()
                photo_due = (
                    self.drive_api is not None
                    and not is_pretend(acc, self.settings.allow_local_accounts)
                    and (
                        acc.photo_updated_at is None
                        or as_utc(acc.photo_updated_at) < self.clock() - timedelta(days=1)
                    )
                )
                email = acc.email
        if photo_due:
            link = await self.drive_api.photo_link(remote)
            await save_photo(
                self.layout,
                self.session_factory,
                account_id,
                email,
                link,
                self.photo_http_factory,
                self.clock,
            )
        with self.session_factory() as db:
            return db.get(Account, account_id)
