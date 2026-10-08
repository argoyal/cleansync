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
        "http://172.16.0.5:8095",
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


def test_scheme_is_case_insensitive_for_https():
    s = Settings(public_url="HTTPS://drivesync.example.com")
    assert s.cookie_secure is True
    assert s.insecure_http is False


def test_repr_never_shows_secret_values():
    s = Settings(
        public_url="https://drivesync.example.com",
        admin_password="pw-" + "x" * 12,
        session_secret="sess-" + "s" * 20,
        rclone_config_pass="rcp-" + "r" * 15,
        google_client_secret="gcs-" + "g" * 15,
    )
    text = repr(s)
    for value in (s.admin_password, s.session_secret, s.rclone_config_pass, s.google_client_secret):
        assert value not in text


def test_empty_secret_file_is_regenerated_not_returned_empty(tmp_path):
    f = tmp_path / "secrets" / "session_secret"
    f.parent.mkdir(parents=True)
    f.write_text("")
    got = resolve_secret("", f)
    assert got != ""
    assert len(got) >= 48
    assert resolve_secret("", f) == got


def test_whitespace_only_secret_file_is_regenerated(tmp_path):
    f = tmp_path / "session_secret"
    f.write_text("  \n")
    got = resolve_secret("", f)
    assert len(got) >= 48


def test_no_temp_files_left_behind(tmp_path):
    f = tmp_path / "session_secret"
    resolve_secret("", f)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["session_secret"]


def test_secret_created_when_hard_links_are_unsupported(tmp_path, monkeypatch):
    import errno

    def no_links(src, dst, *args, **kwargs):
        raise OSError(errno.EPERM, "hard links not supported here")

    monkeypatch.setattr(os, "link", no_links)
    f = tmp_path / "secrets" / "session_secret"
    first = resolve_secret("", f)
    assert len(first) >= 48
    assert resolve_secret("", f) == first
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert sorted(p.name for p in f.parent.iterdir()) == ["session_secret"]
