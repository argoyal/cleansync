from pathlib import Path

import pytest

from app.config import Settings
from app.paths import Layout

_ENV_VARS = (
    "DRIVESYNC_ROOT",
    "PUBLIC_URL",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "SESSION_SECRET",
    "RCLONE_CONFIG_PASS",
    "RCLONE_BINARY",
    "APP_NAME",
    "LOG_LEVEL",
    "TZ",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        drivesync_root=tmp_path / "root",
        public_url="http://localhost:8080",
        admin_username="admin",
        admin_password="initial-pass-123",
        google_client_id="cid",
        google_client_secret="csecret",
    )


@pytest.fixture
def layout(settings: Settings) -> Layout:
    lay = Layout(settings.drivesync_root)
    lay.ensure()
    return lay


from collections.abc import Callable, Iterator

from sqlalchemy.orm import Session, sessionmaker

from app.database.engine import make_engine
from app.database.migrate import run_migrations
from app.models import Account, SyncPair


@pytest.fixture
def engine(layout: Layout):
    run_migrations(layout.db_file)
    eng = make_engine(layout.db_file)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def db(session_factory) -> Iterator[Session]:
    with session_factory() as s:
        yield s


@pytest.fixture
def make_account(db: Session, layout: Layout) -> Callable[..., Account]:
    def _make(email: str = "alice@example.com", remote_name: str | None = None) -> Account:
        acc = Account(email=email, remote_name=remote_name or f"gd_{email.split('@')[0]}")
        db.add(acc)
        db.commit()
        layout.account_dir(email).mkdir(parents=True, exist_ok=True)
        return acc
    return _make


@pytest.fixture
def make_pair(db: Session) -> Callable[..., SyncPair]:
    def _make(account: Account, **overrides) -> SyncPair:
        fields = {"name": "Docs", "local_subpath": "", "remote_path": ""} | overrides
        pair = SyncPair(account_id=account.id, **fields)
        db.add(pair)
        db.commit()
        return pair
    return _make
