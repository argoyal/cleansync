import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Iterator

log = logging.getLogger(__name__)
Handler = Callable[[int, str, str], Awaitable[object]]


class SyncQueue:
    """At most one job per pair; extra sync requests while running collapse into one re-run.

    Must be called from the event loop thread.
    """

    def __init__(self, handler: Handler, max_parallel: int = 2) -> None:
        self._handler = handler
        self._sem = asyncio.Semaphore(max_parallel)
        self._pending: dict[int, tuple[str, str]] = {}
        self._running: set[int] = set()
        self._dirty: dict[int, str] = {}
        self._finished: dict[int, float] = {}
        self._tasks: set[asyncio.Task] = set()
        self._finish_listeners: list[Callable[[int], None]] = []
        # Set when syncing must stop entirely (e.g. the database failed its integrity check).
        self.disabled_reason: str | None = None

    def request(self, pair_id: int, trigger: str, action: str = "sync") -> str:
        if self.disabled_reason is not None:
            return "disabled"
        if pair_id in self._running:
            if action != "sync":
                return "busy"
            self._dirty[pair_id] = trigger
            return "dirty"
        if pair_id in self._pending:
            # Never silently drop a user's resync/preview/confirm behind a different job.
            if action != "sync" and action != self._pending[pair_id][0]:
                return "busy"
            return "already_queued"
        self._pending[pair_id] = (action, trigger)
        task = asyncio.get_running_loop().create_task(self._run(pair_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return "queued"

    def add_finish_listener(self, listener: Callable[[int], None]) -> None:
        """Call listener(pair_id) on the event loop whenever a job for a pair ends."""
        self._finish_listeners.append(listener)

    def is_running(self, pair_id: int) -> bool:
        return pair_id in self._running

    def is_busy(self, pair_id: int) -> bool:
        """Running, or queued and waiting for a free slot."""
        return pair_id in self._running or pair_id in self._pending

    @contextlib.contextmanager
    def hold(self, pair_id: int) -> Iterator[None]:
        """Keep jobs for an idle pair from starting while the app changes its files.

        The pair counts as running meanwhile: a sync asked for during the hold runs right after.
        """
        if self.is_busy(pair_id):
            raise RuntimeError(f"pair {pair_id} is busy")
        self._running.add(pair_id)
        try:
            yield
        finally:
            self._running.discard(pair_id)
            again = self._dirty.pop(pair_id, None)
            if again is not None:
                self.request(pair_id, again)

    def finished_at(self, pair_id: int) -> float | None:
        return self._finished.get(pair_id)

    async def _run(self, pair_id: int) -> None:
        async with self._sem:
            action, trigger = self._pending.pop(pair_id)
            self._running.add(pair_id)
            try:
                await self._handler(pair_id, action, trigger)
            except Exception:
                # The engine already marked the job FAILED and the pair ERROR; keep going.
                log.exception("job for pair %s (%s) failed unexpectedly", pair_id, action)
            finally:
                self._finished[pair_id] = time.monotonic()
                self._running.discard(pair_id)
                for listener in self._finish_listeners:
                    try:
                        listener(pair_id)
                    except Exception:
                        log.exception("finish listener failed for pair %s", pair_id)
                again = self._dirty.pop(pair_id, None)
                if again is not None:
                    self.request(pair_id, again)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def stop(self) -> None:
        self._dirty.clear()
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)


def make_handler(engine, initial, on_change: Callable[[], None] = lambda: None) -> Handler:
    async def handle(pair_id: int, action: str, trigger: str) -> object:
        if action == "sync":
            return await engine.run_pair(pair_id, trigger)
        if action == "preview":
            return await initial.preview(pair_id)
        if action == "initial":
            try:
                return await initial.run_initial(pair_id)
            finally:
                on_change()  # the pair may now be initialized: let the scheduler pick it up
        if action == "resync":
            try:
                return await initial.safe_resync(pair_id, trigger)
            finally:
                on_change()
        raise ValueError(f"unknown action {action}")

    return handle
