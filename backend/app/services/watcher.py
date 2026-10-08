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
        self._observer = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._debouncer: Debouncer | None = None
        self._lock = threading.Lock()

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
            self.status = "limited"
            return
        self.status = "active"
        self.reload()

    def reload(self) -> None:
        with self._lock:
            observer = self._observer
            if observer is None:
                return
            try:
                observer.unschedule_all()
                roots: dict[str, int] = {}
                with self.session_factory() as db:
                    for pair in db.scalars(select(SyncPair)):
                        account = db.get(Account, pair.account_id)
                        if not is_eligible(pair, account):
                            continue
                        local = pair_local_dir(self.layout, account, pair)
                        if local.is_dir():
                            roots[str(local)] = pair.id
                self._roots = roots
                for root in roots:
                    observer.schedule(_Handler(self._from_thread), root, recursive=True)
            except Exception:
                # e.g. inotify watch limit or permission errors: keep the app running
                log.exception("could not watch some folders; relying on the schedule")
                self.status = "limited"

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
            return  # rclone itself is writing; the running job already covers these changes
        finished = self.queue.finished_at(pair_id)
        if finished is not None and time.monotonic() - finished < self.debounce_s + 1:
            return  # tail of events from the job that just ended
        self.queue.request(pair_id, "WATCHER")

    def stop(self) -> None:
        if self._debouncer:
            self._debouncer.cancel_all()
        with self._lock:
            observer, self._observer = self._observer, None
        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=5)
            except Exception:
                log.exception("error while stopping the file watcher")


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
