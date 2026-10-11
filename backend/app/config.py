import ipaddress
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_local_host(host: str) -> bool:
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".lan"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


_APP_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    drivesync_root: Path = Path("/drivesync")
    public_url: str = "http://localhost:8080"
    google_client_id: str = ""
    google_client_secret: str = Field(default="", repr=False)
    admin_username: str = "admin"
    admin_password: str = Field(default="", repr=False)
    rclone_config_pass: str = Field(default="", repr=False)
    rclone_binary: str = "rclone"
    rclone_password_command: str = ""
    app_name: str = "CleanSync"
    # Development and CI only: lets the seed tool add a "pretend Google account" backed by a
    # local folder. Leave it off in production.
    drivesync_allow_local_accounts: bool = False
    log_level: str = "INFO"
    # Container time zone (TZ), used for log timestamps only; syncing always works in UTC.
    tz: str = "UTC"
    # The built web app (frontend/dist) and the blank Office files for "+ New".
    frontend_dir: Path = _APP_DIR.parents[1] / "frontend" / "dist"
    office_templates_dir: Path = _APP_DIR / "resources" / "office"

    @model_validator(mode="after")
    def _check_public_url(self) -> "Settings":
        self.public_url = self.public_url.rstrip("/")
        parsed = urlparse(self.public_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("PUBLIC_URL must look like https://drivesync.example.com")
        if parsed.scheme == "http" and not _is_local_host(parsed.hostname):
            raise ValueError("PUBLIC_URL must use https unless it is localhost or a LAN address")
        return self

    @property
    def allow_local_accounts(self) -> bool:
        return self.drivesync_allow_local_accounts

    @allow_local_accounts.setter
    def allow_local_accounts(self, value: bool) -> None:
        self.drivesync_allow_local_accounts = value

    @property
    def _scheme(self) -> str:
        return urlparse(self.public_url).scheme

    @property
    def cookie_secure(self) -> bool:
        return self._scheme == "https"

    @property
    def insecure_http(self) -> bool:
        return not self.cookie_secure

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.public_url}/api/oauth/callback"
