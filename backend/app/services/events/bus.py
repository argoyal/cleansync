import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import as_utc
from app.core.redact import redact
from app.models import Event

log = logging.getLogger(__name__)


class EventBus:
    """In-process fan-out of events to SSE subscribers.

    publish() is safe to call from any thread (sync endpoints run in a threadpool).
    The loop is bound lazily on the first subscribe().
    """

    def __init__(self, maxsize: int = 1000) -> None:
        self._maxsize = maxsize
        self._subs: set[asyncio.Queue[tuple[str, dict]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, kind: str, data: dict) -> None:
        loop = self._loop
        if loop is None or not self._subs:
            return
        try:
            running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(kind, data)
            return
        try:
            loop.call_soon_threadsafe(self._deliver, kind, data)
        except RuntimeError:
            # The loop is closed (server shutting down); nobody is listening.
            return

    def _deliver(self, kind: str, data: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait((kind, data))
            except asyncio.QueueFull:
                # Log the kind only; the payload may contain sensitive text.
                log.debug("dropped %s event for a slow subscriber", kind)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[tuple[str, dict]]]:
        self._loop = asyncio.get_running_loop()
        q: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(self._maxsize)
        self._subs.add(q)
        try:
            yield q
        finally:
            self._subs.discard(q)


def event_to_dict(ev: Event) -> dict:
    return {
        "id": ev.id,
        "ts": as_utc(ev.ts).isoformat(),
        "level": ev.level,
        "kind": ev.kind,
        "message": ev.message,
        "account_id": ev.account_id,
        "pair_id": ev.pair_id,
        "data": ev.data,
    }


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def record_event(
    db: Session,
    bus: EventBus,
    *,
    kind: str,
    message: str,
    level: str = "info",
    account_id: int | None = None,
    pair_id: int | None = None,
    data: dict | None = None,
) -> Event:
    ev = Event(
        kind=kind,
        message=redact(message),
        level=level,
        account_id=account_id,
        pair_id=pair_id,
        data=_redact_value(data) if data is not None else None,
    )
    db.add(ev)
    db.commit()
    bus.publish("activity", event_to_dict(ev))
    return ev


async def event_stream(
    bus: EventBus, is_disconnected: Callable[[], Awaitable[bool]]
) -> AsyncIterator[dict]:
    async with bus.subscribe() as q:
        while not await is_disconnected():
            try:
                kind, data = await asyncio.wait_for(q.get(), timeout=15)
            except TimeoutError:
                continue
            yield {"event": kind, "data": json.dumps(data, default=str)}
