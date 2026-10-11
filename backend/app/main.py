import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app.api import (
    accounts,
    activity_jobs,
    auth,
    browse,
    catalog,
    conflicts,
    docs,
    events,
    fileops,
    files,
    google,
    health,
    jobs,
    mail,
    mail_offload,
    mail_reader,
    offloads,
    pairs,
    recovery,
    serve,
    setup,
    system,
)
from app.auth.bootstrap import ensure_admin
from app.auth.ratelimit import LoginLimiter
from app.config import Settings
from app.core.logsetup import configure_logging
from app.database.engine import integrity_ok, make_engine
from app.database.migrate import run_migrations
from app.models.enums import Provider
from app.paths import Layout
from app.services.accounts.service import AccountService
from app.services.adapters.files import FilesAdapters, GoogleDriveFiles
from app.services.app_settings import get_setting
from app.services.catalog.service import CatalogService
from app.services.catalog.store import CatalogStore
from app.services.drive_api.client import DriveApi
from app.services.drive_api.tokens import TokenProvider
from app.services.events.bus import EventBus
from app.services.files.changes import FileChanges
from app.services.files.moves import DriveMover
from app.services.files.status import StatusIndexes
from app.services.files.thumbs import Thumbnails
from app.services.files.upload import clear_uploads, sweep_upload_leftovers
from app.services.google.client import GoogleClient
from app.services.google.quota import ApiBudget, Pacer
from app.services.mail.adapters import MailAdapters
from app.services.mail.jobs import recover_mail_jobs
from app.services.mail.offload import MailOffloads, recover_mail_restores
from app.services.mail.offload_plan import recover_mail_offloads
from app.services.mail.service import MailService
from app.services.mail.store import sweep_temp_files
from app.services.offload.service import OffloadService
from app.services.offload.store import recover_offloads, sweep_delete_lists
from app.services.queue import DB_BROKEN_MSG, SyncQueue, make_handler
from app.services.rclone.setup import build_rclone_env, ensure_config_encrypted
from app.services.scheduler import Scheduler
from app.services.sync.engine import SyncEngine
from app.services.sync.initial import InitialSync
from app.services.sync.recovery import recover_interrupted
from app.services.volume import open_volume
from app.services.watcher import LocalWatcher, self_test
from app.web.security import SecurityHeaders
from app.web.spa import mount_spa

logger = logging.getLogger(__name__)


def _log_sweep(task: asyncio.Future) -> None:
    if task.cancelled():
        return
    if task.exception() is not None:
        logger.warning("could not sweep old upload copies: %r", task.exception())
    elif task.result():
        logger.info("removed %s upload copies left by an earlier crash", task.result())


def create_app(settings: Settings | None = None, *, start_background: bool = True) -> FastAPI:
    settings = settings or Settings()
    redactor = configure_logging(
        settings.log_level,
        [settings.google_client_secret, settings.admin_password, settings.rclone_config_pass],
    )
    layout = Layout(settings.drivesync_root)
    layout.ensure()
    clear_uploads(layout)
    run_migrations(layout.db_file)
    engine = make_engine(layout.db_file)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.catalog.bind_loop(asyncio.get_running_loop())
        try:
            await ensure_config_encrypted(app.state.rclone_env, app.state.accounts.runner)
        except Exception as e:  # never stop the app over this; the next connect retries
            logger.error("could not encrypt rclone.conf at startup: %s", e)
        if not app.state.db_ok:
            logger.error(
                "database integrity check failed; syncing is disabled. "
                "Restore a copy from app/data/db-backups (see docs/backup-recovery.md)."
            )
        elif app.state.queue.disabled_reason is not None:
            # No recovery runs, no schedule, no watcher, no walking a disk that isn't there.
            logger.error("%s", app.state.queue.disabled_reason)
            # The database itself is sound: close jobs a crash left "running", so no pair shows
            # as running forever. Nothing is queued to run again.
            # Each step on its own: one failing must not skip the others.
            try:
                with session_factory() as db:
                    recover_interrupted(db)
            except Exception:
                logger.exception("could not close jobs left running")
            try:
                with session_factory() as db:
                    # An offload a stop cut short stays out of sync and shows as needing a look.
                    recover_offloads(db)
            except Exception:
                logger.exception("could not mark offloads a stop cut short")
            try:
                with session_factory() as db:
                    recover_mail_restores(db)
            except Exception:
                logger.exception("could not close mail restores a restart cut short")
            try:
                with session_factory() as db:
                    recover_mail_offloads(db)  # first: it closes its own job as failed
                    recover_mail_jobs(db)
            except Exception:
                logger.exception("could not close mail jobs left running")
            try:
                sweep_delete_lists(layout)
            except Exception:
                logger.exception("could not remove old offload delete lists")
        elif start_background:
            # Walking all of storage/ can take a while on a slow disk: don't hold up startup.
            sweep = asyncio.ensure_future(asyncio.to_thread(sweep_upload_leftovers, layout.storage))
            sweep.add_done_callback(_log_sweep)
            with session_factory() as db:
                recover_offloads(db)
                for pair_id in recover_interrupted(db):
                    app.state.queue.request(pair_id, "RECOVERY")
                try:
                    recover_mail_offloads(db)  # before recover_mail_jobs: it closes its job
                except Exception:
                    db.rollback()
                    logger.exception("could not mark mail offloads a stop cut short")
                try:
                    recover_mail_restores(db)
                except Exception:
                    db.rollback()
                    logger.exception("could not close mail restores a restart cut short")
                # A mail job a stop cut short goes on where it left off.
                for account_id in recover_mail_jobs(db):
                    app.state.mail.queue.request(account_id, "backup", trigger="RECOVERY")
            try:
                sweep_delete_lists(layout)
            except OSError as e:  # harmless leftovers: never stop startup over them
                logger.warning("could not remove old offload delete lists: %s", e)
            try:
                sweep_temp_files(layout)
            except OSError as e:
                logger.warning("could not remove half-written mail files: %s", e)
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
            app.state.file_changes.stop()
            # Offloads first: a cancelled step lets go of its pair (and may ask for a sync)
            # while the queue still runs, and the queue's stop then ends anything it started.
            await app.state.offloads.stop()
            await app.state.mail.stop()  # also saves the Google API units counted so far
            await app.state.queue.stop()
            await app.state.catalog.stop()
            app.state.budget.flush()

    app = FastAPI(
        lifespan=lifespan,
        title=settings.app_name,
        # The built-in docs are public; app.api.docs serves them behind the login instead.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    if settings.allow_local_accounts:
        logger.warning(
            "DRIVESYNC_ALLOW_LOCAL_ACCOUNTS is on: pretend local accounts can be added. "
            "This is for development and CI only; do not use it in production."
        )
    app.state.layout = layout
    app.state.engine_db = engine
    app.state.session_factory = session_factory
    app.state.db_ok = integrity_ok(engine)
    with session_factory() as db:
        ensure_admin(db, settings, layout)
        # Only trust the database's ID when the database itself is sound.
        volume = open_volume(db, layout) if app.state.db_ok else None
        max_parallel = int(get_setting(db, "max_parallel_pairs"))  # type: ignore[arg-type]
        debounce = int(get_setting(db, "watcher_debounce_s"))  # type: ignore[arg-type]
    app.state.volume = volume
    app.state.login_limiter = LoginLimiter()
    app.state.bus = EventBus()
    app.state.rclone_env = build_rclone_env(settings, layout)
    redactor.add_secret(app.state.rclone_env.config_pass)  # may have been generated just now
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
    app.state.catalog_store = CatalogStore(layout.catalog_dir)  # one instance: it holds the lock
    app.state.catalog = CatalogService(
        layout=layout,
        session_factory=session_factory,
        bus=app.state.bus,
        rclone_env=app.state.rclone_env,
        accounts=app.state.accounts,
        store=app.state.catalog_store,
    )

    def _on_change() -> None:
        app.state.scheduler.reload()
        # the watcher reads the database and disk: keep that off the event loop
        asyncio.get_running_loop().run_in_executor(None, app.state.watcher.reload)

    app.state.drive_mover = DriveMover(
        layout=layout, env=app.state.rclone_env, accounts=app.state.accounts
    )
    app.state.indexes = StatusIndexes(layout=layout, catalog=app.state.catalog)
    app.state.catalog.listeners.append(app.state.indexes.invalidate)
    # Follow-ups of file actions: one "files" event per burst, one sync after it.
    app.state.file_changes = FileChanges(
        bus=app.state.bus,
        get_queue=lambda: app.state.queue,
        indexes=app.state.indexes,
        sync_delay_s=debounce,
    )
    app.state.indexes.on_refreshed = lambda pair: app.state.file_changes.notify(pair.account_id)
    app.state.thumbs = Thumbnails(layout.thumbs_dir)

    def _budget_setting() -> int:
        with session_factory() as db:
            return int(get_setting(db, "google_daily_budget_units"))  # type: ignore[arg-type]

    app.state.budget = ApiBudget(session_factory, budget=_budget_setting)
    app.state.pacer = Pacer()
    app.state.google = GoogleClient(budget=app.state.budget, pacer=app.state.pacer)
    app.state.tokens = TokenProvider(settings=settings, rclone_env=app.state.rclone_env)
    app.state.drive_api = DriveApi(app.state.tokens, google=app.state.google)
    app.state.engine.on_api_estimate = app.state.budget.add_drive_estimate
    app.state.accounts.on_login_changed = app.state.tokens.forget
    # Spec §3.2: the Drive calls made outside a sync run go through the Files adapter.
    app.state.files = FilesAdapters(
        {
            Provider.GOOGLE: GoogleDriveFiles(
                lambda: app.state.drive_api, lambda: app.state.drive_mover
            )
        }
    )
    app.state.queue = SyncQueue(
        make_handler(
            app.state.engine,
            app.state.initial,
            on_change=_on_change,
        ),
        max_parallel=max_parallel,
        guard=volume.check if volume is not None else None,
        # Nothing is written to the database here: it lives on the disk that just went away.
        on_stop=lambda reason: app.state.bus.publish("pair", {"sync_stopped": reason}),
        # Whatever ends a job, nothing it opened stays RUNNING (spec §4.5, the stuck busy flag).
        settle=app.state.engine.settle_pair,
    )
    if not app.state.db_ok:
        app.state.queue.disabled_reason = DB_BROKEN_MSG
    elif volume is not None:
        app.state.queue.disabled_reason = volume.check()
    app.state.offloads = OffloadService(
        engine=app.state.engine,
        get_queue=lambda: app.state.queue,  # tests swap the queue after startup
        catalog=app.state.catalog,
        indexes=app.state.indexes,
        drive=app.state.files.google,
        allow_pretend=settings.allow_local_accounts,
    )
    # A Restore to Drive finishes when the pair's safe resync has uploaded the files.
    app.state.initial.on_resync_done.append(app.state.offloads.resync_done)
    app.state.mail = MailService(
        layout=layout,
        session_factory=session_factory,
        bus=app.state.bus,
        accounts=app.state.accounts,
        adapters=MailAdapters(
            google=app.state.google,
            tokens=app.state.tokens,
            layout=layout,
            allow_pretend=settings.allow_local_accounts,
        ),
        budget=app.state.budget,
        get_sync_queue=lambda: app.state.queue,  # tests swap the queue after startup
    )
    app.state.mail_offloads = MailOffloads(app.state.mail)
    app.state.scheduler = Scheduler(
        session_factory=session_factory,
        queue=app.state.queue,
        accounts=app.state.accounts,
        layout=layout,
        catalog=app.state.catalog,
        mail=app.state.mail,
    )
    # Reconnecting, disconnecting or losing a Google login changes which pairs can run.
    app.state.accounts.on_change = _on_change
    app.state.watcher = LocalWatcher(
        session_factory=session_factory,
        layout=layout,
        queue=app.state.queue,
        debounce_s=debounce,
    )
    # rclone's own writes during a job must not queue a follow-up sync once it ends.
    app.state.queue.add_finish_listener(app.state.watcher.job_finished)
    # A run that changed files makes the Drive listing stale.
    app.state.queue.add_finish_listener(app.state.catalog.on_job_finished)
    app.state.queue.add_finish_listener(app.state.indexes.invalidate)  # downloads change the disk
    for router in (
        health.router,
        auth.router,
        events.router,
        accounts.router,
        pairs.router,
        browse.router,
        catalog.router,
        files.router,
        fileops.router,
        serve.router,
        setup.router,
        jobs.router,
        offloads.router,
        recovery.router,
        conflicts.router,
        mail.router,
        mail_reader.router,
        mail_offload.router,
        activity_jobs.router,
        system.router,
        google.router,
        docs.router,
    ):
        app.include_router(router)
    app.add_middleware(SecurityHeaders)
    mount_spa(app, settings.frontend_dir)  # last: its catch-all route must not shadow the API
    return app
