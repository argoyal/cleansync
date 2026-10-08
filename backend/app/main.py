import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app.api import accounts, auth, browse, events, health, pairs
from app.auth.bootstrap import ensure_admin
from app.auth.ratelimit import LoginLimiter
from app.config import Settings
from app.database.engine import integrity_ok, make_engine
from app.database.migrate import run_migrations
from app.paths import Layout
from app.services.accounts.service import AccountService
from app.services.events.bus import EventBus
from app.services.rclone.setup import build_rclone_env, ensure_config_encrypted

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        await ensure_config_encrypted(app.state.rclone_env, app.state.accounts.runner)
    except Exception as e:  # never stop the app over this; the next connect retries
        logger.error("could not encrypt rclone.conf at startup: %s", e)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    layout = Layout(settings.drivesync_root)
    layout.ensure()
    run_migrations(layout.db_file)
    engine = make_engine(layout.db_file)
    app = FastAPI(
        lifespan=_lifespan,
        title=settings.app_name,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.layout = layout
    app.state.engine_db = engine
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    app.state.db_ok = integrity_ok(engine)
    with app.state.session_factory() as db:
        ensure_admin(db, settings, layout)
    app.state.login_limiter = LoginLimiter()
    app.state.bus = EventBus()
    app.state.rclone_env = build_rclone_env(settings, layout)
    app.state.accounts = AccountService(
        settings=settings,
        layout=layout,
        session_factory=app.state.session_factory,
        bus=app.state.bus,
        rclone_env=app.state.rclone_env,
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(events.router)
    app.include_router(accounts.router)
    app.include_router(pairs.router)
    app.include_router(browse.router)
    return app
