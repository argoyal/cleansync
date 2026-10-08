import asyncio
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
from app.services.app_settings import get_setting
from app.services.events.bus import EventBus
from app.services.queue import SyncQueue, make_handler
from app.services.rclone.setup import build_rclone_env, ensure_config_encrypted
from app.services.scheduler import Scheduler
from app.services.sync.engine import SyncEngine
from app.services.sync.initial import InitialSync
from app.services.sync.recovery import recover_interrupted
from app.services.watcher import LocalWatcher, self_test

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, start_background: bool = True) -> FastAPI:
    settings = settings or Settings()
    layout = Layout(settings.drivesync_root)
    layout.ensure()
    run_migrations(layout.db_file)
    engine = make_engine(layout.db_file)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await ensure_config_encrypted(app.state.rclone_env, app.state.accounts.runner)
        except Exception as e:  # never stop the app over this; the next connect retries
            logger.error("could not encrypt rclone.conf at startup: %s", e)
        if not app.state.db_ok:
            logger.error(
                "database integrity check failed; syncing is disabled. "
                "Restore a copy from app/data/db-backups (see docs/backup-recovery.md)."
            )
        elif start_background:
            with session_factory() as db:
                for pair_id in recover_interrupted(db):
                    app.state.queue.request(pair_id, "RECOVERY")
            app.state.scheduler.start()
            try:
                active = await self_test(layout.storage)
                app.state.watcher.events_ok = active
                app.state.watcher.start(asyncio.get_running_loop())
                if not active:
                    logger.warning(
                        "file change notifications do not work for %s; local changes will "
                        "be picked up by the schedule instead",
                        layout.storage,
                    )
            except Exception:
                logger.exception("file watcher setup failed; relying on the schedule")
                app.state.watcher.status = "limited"
        try:
            yield
        finally:
            app.state.scheduler.shutdown()
            app.state.watcher.stop()
            await app.state.queue.stop()

    app = FastAPI(
        lifespan=lifespan,
        title=settings.app_name,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.layout = layout
    app.state.engine_db = engine
    app.state.session_factory = session_factory
    app.state.db_ok = integrity_ok(engine)
    with session_factory() as db:
        ensure_admin(db, settings, layout)
        max_parallel = int(get_setting(db, "max_parallel_pairs"))  # type: ignore[arg-type]
        debounce = int(get_setting(db, "watcher_debounce_s"))  # type: ignore[arg-type]
    app.state.login_limiter = LoginLimiter()
    app.state.bus = EventBus()
    app.state.rclone_env = build_rclone_env(settings, layout)
    app.state.accounts = AccountService(
        settings=settings,
        layout=layout,
        session_factory=session_factory,
        bus=app.state.bus,
        rclone_env=app.state.rclone_env,
    )
    app.state.engine = SyncEngine(
        layout=layout,
        session_factory=session_factory,
        bus=app.state.bus,
        rclone_env=app.state.rclone_env,
        accounts=app.state.accounts,
    )
    app.state.initial = InitialSync(app.state.engine)

    def _on_change() -> None:
        app.state.scheduler.reload()
        # the watcher reads the database and disk: keep that off the event loop
        asyncio.get_running_loop().run_in_executor(None, app.state.watcher.reload)

    app.state.queue = SyncQueue(
        make_handler(
            app.state.engine,
            app.state.initial,
            on_change=_on_change,
        ),
        max_parallel=max_parallel,
    )
    if not app.state.db_ok:
        app.state.queue.disabled_reason = "database integrity check failed"
    app.state.scheduler = Scheduler(
        session_factory=session_factory,
        queue=app.state.queue,
        accounts=app.state.accounts,
        layout=layout,
    )
    app.state.watcher = LocalWatcher(
        session_factory=session_factory,
        layout=layout,
        queue=app.state.queue,
        debounce_s=debounce,
    )
    for router in (
        health.router,
        auth.router,
        events.router,
        accounts.router,
        pairs.router,
        browse.router,
    ):
        app.include_router(router)
    return app
