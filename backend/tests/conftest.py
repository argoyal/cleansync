from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.paths import Layout
from tests.fake_rclone import FakeRclone

_ENV_VARS = (
    "DRIVESYNC_ROOT",
    "PUBLIC_URL",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "RCLONE_CONFIG_PASS",
    "RCLONE_BINARY",
    "RCLONE_PASSWORD_COMMAND",
    "APP_NAME",
    "LOG_LEVEL",
    "TZ",
    "FRONTEND_DIR",
    "OFFICE_TEMPLATES_DIR",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path: Path, fake_rclone) -> Settings:
    passcmd = tmp_path / "print-pass"
    passcmd.write_text('#!/bin/sh\nprintf "%s" "$RCLONE_CONFIG_PASS"\n')
    passcmd.chmod(0o755)
    return Settings(
        drivesync_root=tmp_path / "root",
        public_url="http://localhost:8080",
        admin_username="admin",
        admin_password="initial-pass-123",
        google_client_id="cid",
        google_client_secret="csecret",
        rclone_binary=fake_rclone.binary,
        rclone_password_command=str(passcmd),
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
from app.main import create_app
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


NEW_PASSWORD = "a-much-better-passphrase"


@pytest.fixture
def app(settings):
    return create_app(settings, start_background=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_client(client, settings):
    r = client.post(
        "/api/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": settings.admin_password, "new_password": NEW_PASSWORD},
    )
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return client


@pytest.fixture
def fake_rclone(tmp_path) -> FakeRclone:
    return FakeRclone(tmp_path / "fake-rclone")


from app.services.accounts.service import AccountService
from app.services.events.bus import EventBus
from app.services.rclone.commands import RcloneEnv
from app.services.sync.engine import SyncEngine


@pytest.fixture
def sync_engine(layout, session_factory, settings, fake_rclone) -> SyncEngine:
    bus = EventBus()
    env = RcloneEnv(
        binary=fake_rclone.binary,
        config_file=layout.rclone_conf,
        cache_dir=layout.rclone_cache,
        config_pass="pw",
    )
    accounts = AccountService(
        settings=settings, layout=layout, session_factory=session_factory, bus=bus, rclone_env=env
    )
    return SyncEngine(
        layout=layout, session_factory=session_factory, bus=bus, rclone_env=env, accounts=accounts
    )


from app.services.catalog.service import CatalogService


@pytest.fixture
def catalog_service(sync_engine) -> CatalogService:
    return CatalogService(
        layout=sync_engine.layout,
        session_factory=sync_engine.session_factory,
        bus=sync_engine.bus,
        rclone_env=sync_engine.env,
        accounts=sync_engine.accounts,
    )


class StubQueue:
    """Stands in for SyncQueue in API tests: records requests, never runs anything."""

    def __init__(self):
        self.requests: list[tuple[int, str, str]] = []
        self.answer = "queued"
        self.running: set[int] = set()
        self.disabled_reason: str | None = None

    def request(self, pair_id, trigger, action="sync"):
        self.requests.append((pair_id, trigger, action))
        return self.answer

    def is_running(self, pair_id):
        return pair_id in self.running

    def is_busy(self, pair_id):
        return pair_id in self.running

    def add_finish_listener(self, listener):
        pass

    async def stop(self):
        pass


@pytest.fixture
def stub_queue(app) -> StubQueue:
    q = StubQueue()
    app.state.queue = q
    return q


@pytest.fixture
def save_catalog(app):
    """Write a pair's Drive listing as if rclone had just listed it."""
    from app.core.clock import utcnow
    from app.services.catalog.model import Catalog, CatalogEntry

    def _save(pair_id: int, entries: list[CatalogEntry], root_id: str | None = "root"):
        app.state.catalog.store.save(Catalog.build(pair_id, utcnow(), root_id, entries))

    return _save
