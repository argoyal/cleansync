import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.models import Account, SyncPair
from app.paths import Layout
from app.services.sync.engine import is_eligible
from app.services.sync.pairs import pair_local_dir

log = logging.getLogger(__name__)
_IGNORED_NAMES = {"RCLONE_TEST"}
_IGNORED_DIRS = {".google-exports"}
_IGNORED_SUFFIXES = (".partial",)
_AFTER_JOB_QUIET_S = 2.0  # rclone's late filesystem events after a job ends


def should_ignore(rel_path: str) -> bool:
    p = PurePosixPath(rel_path)
    return (
        p.name in _IGNORED_NAMES
        or any(part in _IGNORED_DIRS for part in p.parts)
        or p.name.endswith(_IGNORED_SUFFIXES)
    )


class Debouncer:
    def __init__(
        self,
        delay_s: float,
        callback: Callable[[int], None],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.delay_s = delay_s
        self.callback = callback
        self.loop = loop
        self._handles: dict[int, asyncio.TimerHandle] = {}

    def touch(self, key: int) -> None:
        old = self._handles.pop(key, None)
        if old is not None:
            old.cancel()
        self._handles[key] = self.loop.call_later(self.delay_s, self._fire, key)

    def _fire(self, key: int) -> None:
        self._handles.pop(key, None)
        self.callback(key)

    def cancel_all(self) -> None:
        for h in self._handles.values():
            h.cancel()
        self._handles.clear()


class _Handler(FileSystemEventHandler):
    def __init__(self, on_path: Callable[[str], None]) -> None:
        self.on_path = on_path

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in ("opened", "closed_no_write"):
            return
        if event.is_directory and event.event_type == "modified":
            return  # folder mtime changes carry no file content and bypass the ignore rules
        self.on_path(str(event.src_path))
        dest = getattr(event, "dest_path", "")
        if dest:
            self.on_path(str(dest))


class LocalWatcher:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        layout: Layout,
        queue,
        debounce_s: float = 5.0,
    ) -> None:
        self.session_factory = session_factory
        self.layout = layout
        self.queue = queue
        self.debounce_s = debounce_s
        self.status = "off"
        self._roots: dict[str, int] = {}
        self._watches: dict[str, object] = {}
        self.events_ok = True  # set False when the startup self-test saw no events
        self._failed = False
        self._observer = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._debouncer: Debouncer | None = None
        self._lock = threading.Lock()

    def _update_status(self) -> None:
        self.status = "active" if (self.events_ok and not self._failed) else "limited"

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Never raises: if watching cannot work, log it and report status 'limited'."""
        self._loop = loop
        self._debouncer = Debouncer(self.debounce_s, self._fire, loop)
        try:
            self._observer = Observer()
            self._observer.start()
        except Exception:
            log.exception("could not start the file watcher; relying on the schedule")
            self._observer = None
            self._failed = True
            self._update_status()
            return
        self._failed = False
        self.reload()

    async def areload(self) -> None:
        """Reload without blocking the event loop (it reads the database and the disk)."""
        await asyncio.to_thread(self.reload)

    def reload(self) -> None:
        """Make the watches match the database: add new roots, drop removed, keep the rest."""
        with self._lock:
            observer = self._observer
            if observer is None:
                return
            wanted: dict[str, int] = {}
            try:
                with self.session_factory() as db:
                    for pair in db.scalars(select(SyncPair)):
                        account = db.get(Account, pair.account_id)
                        if not is_eligible(pair, account):
                            continue
                        local = pair_local_dir(self.layout, account, pair)
                        if local.is_dir():
                            wanted[str(local)] = pair.id
            except Exception:
                log.exception("could not read pairs for the file watcher")
                self._failed = True
                self._update_status()
                return
            failed = False
            for root in list(self._watches):
                if root not in wanted:
                    try:
                        observer.unschedule(self._watches[root])
                    except Exception:
                        log.exception("could not stop watching %s", root)
                    del self._watches[root]
            for root in wanted:
                if root in self._watches:
                    continue
                try:
                    self._watches[root] = observer.schedule(
                        _Handler(self._from_thread), root, recursive=True
                    )
                except Exception:
                    # e.g. inotify watch limit or permission errors: the schedule still covers it
                    log.exception("could not watch %s; relying on the schedule", root)
                    failed = True
            self._roots = {r: wanted[r] for r in self._watches}
            self._failed = failed
            self._update_status()

    def _match(self, path: str) -> tuple[int, str] | None:
        best = None
        for root, pair_id in self._roots.items():
            if path == root or path.startswith(root + "/"):
                if best is None or len(root) > len(best[0]):
                    best = (root, pair_id)
        if best is None:
            return None
        return best[1], path[len(best[0]) :].lstrip("/")

    def _from_thread(self, path: str) -> None:
        hit = self._match(path)
        if hit is None or should_ignore(hit[1]) or self._loop is None or self._debouncer is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._debouncer.touch, hit[0])
        except RuntimeError:  # loop already closed during shutdown
            pass

    def _fire(self, pair_id: int) -> None:
        if self.queue.is_running(pair_id):
            # rclone itself is writing; an edit made during a run is picked up by the
            # next scheduled sync
            return
        finished = self.queue.finished_at(pair_id)
        if finished is not None and time.monotonic() - finished < _AFTER_JOB_QUIET_S:
            return  # tail of events from the job that just ended
        self.queue.request(pair_id, "WATCHER")

    def stop(self) -> None:
        with self._lock:
            observer, self._observer = self._observer, None
            self._watches.clear()
            self._roots = {}
        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=5)
            except Exception:
                log.exception("error while stopping the file watcher")
        if self._debouncer:
            self._debouncer.cancel_all()


async def self_test(directory: Path, timeout_s: float = 3.0) -> bool:
    """True if file events are delivered for this folder (often false on Windows/WSL drives)."""
    directory.mkdir(parents=True, exist_ok=True)
    seen = threading.Event()
    probe = directory / f".drivesync-watch-test-{uuid.uuid4().hex}"
    observer = Observer()
    try:
        observer.schedule(
            _Handler(lambda p: seen.set() if p == str(probe) else None),
            str(directory),
            recursive=False,
        )
        observer.start()
    except Exception:
        log.exception("file change notifications could not be set up for %s", directory)
        return False
    try:
        await asyncio.sleep(0.2)
        probe.write_text("x")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, seen.wait, timeout_s)
    finally:
        probe.unlink(missing_ok=True)
        observer.stop()
        observer.join(timeout=5)
