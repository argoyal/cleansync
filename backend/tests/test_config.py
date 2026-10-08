import os
import stat

import pytest

from app.config import Settings
from app.secrets_store import resolve_secret


def test_https_public_url_gives_secure_cookies_and_redirect():
    s = Settings(public_url="https://drivesync.example.com/")
    assert s.cookie_secure is True
    assert s.insecure_http is False
    assert s.oauth_redirect_uri == "https://drivesync.example.com/api/oauth/callback"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://192.168.0.10:8095",
        "http://10.0.0.5",
        "http://nas.local",
    ],
)
def test_plain_http_allowed_only_for_local_addresses(url):
    s = Settings(public_url=url)
    assert s.cookie_secure is False
    assert s.insecure_http is True


def test_plain_http_on_public_hostname_is_rejected():
    with pytest.raises(ValueError, match="https"):
        Settings(public_url="http://drivesync.example.com")


def test_env_value_wins_over_file(tmp_path):
    f = tmp_path / "s"
    f.write_text("from-file")
    assert resolve_secret("from-env", f) == "from-env"


def test_secret_generated_once_and_reused_with_private_mode(tmp_path):
    f = tmp_path / "secrets" / "session_secret"
    first = resolve_secret("", f)
    assert len(first) >= 48
    assert resolve_secret("", f) == first
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
