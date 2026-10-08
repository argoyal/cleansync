from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app.api import auth, health
from app.auth.bootstrap import ensure_admin
from app.auth.ratelimit import LoginLimiter
from app.config import Settings
from app.database.engine import integrity_ok, make_engine
from app.database.migrate import run_migrations
from app.paths import Layout


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    layout = Layout(settings.drivesync_root)
    layout.ensure()
    run_migrations(layout.db_file)
    engine = make_engine(layout.db_file)
    app = FastAPI(title=settings.app_name, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.layout = layout
    app.state.engine_db = engine
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    app.state.db_ok = integrity_ok(engine)
    with app.state.session_factory() as db:
        ensure_admin(db, settings, layout)
    app.state.login_limiter = LoginLimiter()
    app.include_router(health.router)
    app.include_router(auth.router)
    return app
