import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy.orm import Session

from app.models import Event


class EventBus:
    def __init__(self, maxsize: int = 1000) -> None:
        self._maxsize = maxsize
        self._subs: set[asyncio.Queue[tuple[str, dict]]] = set()

    def publish(self, kind: str, data: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait((kind, data))
            except asyncio.QueueFull:
                pass

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[tuple[str, dict]]]:
        q: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(self._maxsize)
        self._subs.add(q)
        try:
            yield q
        finally:
            self._subs.discard(q)


def event_to_dict(ev: Event) -> dict:
    return {
        "id": ev.id,
        "ts": ev.ts.isoformat(),
        "level": ev.level,
        "kind": ev.kind,
        "message": ev.message,
        "account_id": ev.account_id,
        "pair_id": ev.pair_id,
        "data": ev.data,
    }


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
        kind=kind, message=message, level=level, account_id=account_id, pair_id=pair_id, data=data
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
