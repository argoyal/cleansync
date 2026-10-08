import pytest
from sqlalchemy import select

from app.config import Settings
from app.devtools.seed_local_account import main
from app.models import Account
from app.services.rclone.commands import UnsafeArgument


def test_setup_reports_what_the_wizard_needs(auth_client, make_account, make_pair):
    s = auth_client.get("/api/setup").json()
    assert s == {
        "oauth_configured": True,
        "redirect_uri": "http://localhost:8080/api/oauth/callback",
        "public_url": "http://localhost:8080",
        "insecure_http": True,
        "accounts": 0,
        "pairs": 0,
        "initialized_pairs": 0,
    }
    make_pair(make_account(), initialized=True)
    s = auth_client.get("/api/setup").json()
    assert (s["accounts"], s["pairs"], s["initialized_pairs"]) == (1, 1, 1)


def test_setup_needs_a_changed_password(client, settings):
    client.post("/api/auth/login", json={"username": "admin", "password": settings.admin_password})
    assert client.get("/api/setup").status_code == 403


@pytest.fixture
def seed_env(settings, monkeypatch, fake_rclone):
    monkeypatch.setenv("DRIVESYNC_ROOT", str(settings.drivesync_root))
    monkeypatch.setenv("RCLONE_BINARY", fake_rclone.binary)
    monkeypatch.setenv("RCLONE_PASSWORD_COMMAND", settings.rclone_password_command)
    monkeypatch.setenv("RCLONE_CONFIG_PASS", "a-long-test-passphrase")


def test_seed_adds_a_local_account(seed_env, monkeypatch, fake_rclone, db, tmp_path, capsys):
    monkeypatch.setenv("DRIVESYNC_ALLOW_LOCAL_ACCOUNTS", "1")
    folder = tmp_path / "pretend-drive"
    assert main(["Alice@Example.com", "--folder", str(folder), "--sample-files"]) == 0
    acc = db.scalar(select(Account).where(Account.email == "alice@example.com"))
    assert acc.remote_name.startswith("local_") and acc.status == "CONNECTED"
    assert (folder / "Photos" / "sky.png").is_file() and (
        folder / "Reports" / "summary.pdf"
    ).is_file()
    create = [c for c in fake_rclone.calls() if c[4:6] == ["config", "create"]][0]
    assert create[6:10] == [acc.remote_name, "alias", "remote", str(folder.resolve())]
    assert "pretend Google Drive" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="already connected"):
        main(["alice@example.com"])


def test_seed_refuses_unless_the_dev_flag_is_on(seed_env, db):
    with pytest.raises(SystemExit, match="DRIVESYNC_ALLOW_LOCAL_ACCOUNTS"):
        main(["alice@example.com"])
    assert db.scalar(select(Account)) is None


def test_the_dev_flag_is_off_by_default():
    assert Settings().allow_local_accounts is False


def test_seed_refuses_a_folder_with_a_newline(seed_env, monkeypatch, db, tmp_path):
    monkeypatch.setenv("DRIVESYNC_ALLOW_LOCAL_ACCOUNTS", "1")
    with pytest.raises(UnsafeArgument):
        main(["alice@example.com", "--folder", str(tmp_path / "a\nb")])
    assert db.scalar(select(Account)) is None


def test_adding_a_local_account_tells_the_scheduler_and_token_cache(
    settings, layout, session_factory, fake_rclone, tmp_path
):
    import asyncio

    from app.services.accounts.service import AccountService
    from app.services.events.bus import EventBus
    from app.services.rclone.setup import build_rclone_env

    settings.allow_local_accounts = True
    changed, logins = [], []
    svc = AccountService(
        settings=settings,
        layout=layout,
        session_factory=session_factory,
        bus=EventBus(),
        rclone_env=build_rclone_env(settings, layout),
        on_change=lambda: changed.append(1),
        on_login_changed=logins.append,
    )
    acc = asyncio.run(svc.add_local_account("bob@example.com", tmp_path / "bobs-drive"))
    assert changed == [1] and logins == [acc.remote_name]
