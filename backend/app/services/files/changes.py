"""Follow-ups of file actions from the UI, coalesced.

A folder upload is one request per file. Without this, each file would tell every open tab
to reload ("files" event) and ask for a sync of its pair. Instead:

- "files" events: the first goes out at once, the rest of a burst become one more event at
  the end of a short window (per account).
- syncs: asked for once the burst has been quiet for the watcher's debounce time (per pair),
  like a burst of changes the file watcher sees. The pair's cached statuses are dropped at
  the same moment, once; until then the browser judges new items on the spot.

Everything here runs on the event loop thread.
"""

import asyncio
import logging
import time
from collections.abc import Callable

from app.models.enums import Trigger

log = logging.getLogger(__name__)
EVENT_WINDOW_S = 1.5


class FileChanges:
    def __init__(
        self,
        *,
        bus,
        get_queue: Callable[[], object],
        indexes,
        sync_delay_s: float,
        event_window_s: float = EVENT_WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bus = bus
        self.get_queue = get_queue  # read at fire time: the queue object may be swapped
        self.indexes = indexes
        self.sync_delay_s = sync_delay_s
        self.event_window_s = event_window_s
        self.clock = clock
        self._last_event: dict[int, float] = {}
        self._event_later: dict[int, asyncio.TimerHandle] = {}
        self._sync_later: dict[int, tuple[asyncio.TimerHandle, bool]] = {}

    # ---- "files" events -------------------------------------------------------------------

    def notify(self, account_id: int) -> None:
        """Tell open tabs that this account's files changed (coalesced)."""
        if account_id in self._event_later:
            return  # an event at the end of this window is already planned
        last = self._last_event.get(account_id)
        since = None if last is None else self.clock() - last
        if since is None or since >= self.event_window_s:
            self._publish(account_id)
            return
        loop = asyncio.get_running_loop()
        self._event_later[account_id] = loop.call_later(
            self.event_window_s - since, self._publish_later, account_id
        )

    def _publish_later(self, account_id: int) -> None:
        self._event_later.pop(account_id, None)
        self._publish(account_id)

    def _publish(self, account_id: int) -> None:
        self._last_event[account_id] = self.clock()
        self.bus.publish("files", {"account_id": account_id})

    # ---- syncs ----------------------------------------------------------------------------

    def request_sync(self, pair_id: int, *, initialized: bool) -> str:
        """Ask for one APP sync of the pair after the burst; returns what the UI is told."""
        if self.sync_delay_s <= 0:
            self.indexes.invalidate(pair_id)
            return self._sync(pair_id) if initialized else "not_initialized"
        old = self._sync_later.pop(pair_id, None)
        if old is not None:
            old[0].cancel()
            initialized = initialized or old[1]
        loop = asyncio.get_running_loop()
        handle = loop.call_later(self.sync_delay_s, self._fire, pair_id)
        self._sync_later[pair_id] = (handle, initialized)
        return "scheduled" if initialized else "not_initialized"

    def _fire(self, pair_id: int) -> None:
        pending = self._sync_later.pop(pair_id, None)
        self.indexes.invalidate(pair_id)
        if pending is not None and pending[1]:
            try:
                self._sync(pair_id)
            except Exception:
                log.exception("could not queue the sync of pair %s after a file action", pair_id)

    def _sync(self, pair_id: int) -> str:
        return self.get_queue().request(pair_id, Trigger.APP)  # type: ignore[attr-defined]

    def stop(self) -> None:
        """Drop planned work (shutdown); the next scheduled sync picks the changes up."""
        for handle in self._event_later.values():
            handle.cancel()
        for handle, _ in self._sync_later.values():
            handle.cancel()
        self._event_later.clear()
        self._sync_later.clear()
