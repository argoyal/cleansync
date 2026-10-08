import asyncio
import json

from app.services.events.bus import EventBus, event_stream, record_event


async def test_publish_reaches_subscriber():
    bus = EventBus()
    async with bus.subscribe() as q:
        bus.publish("job", {"pair_id": 1})
        assert await asyncio.wait_for(q.get(), 1) == ("job", {"pair_id": 1})


async def test_slow_subscriber_does_not_block_publisher():
    bus = EventBus(maxsize=2)
    async with bus.subscribe() as q:
        for i in range(10):
            bus.publish("progress", {"i": i})
        assert q.qsize() == 2


async def test_stream_yields_sse_dicts_until_disconnect():
    bus = EventBus()
    state = {"gone": False}

    async def is_disconnected():
        return state["gone"]

    gen = event_stream(bus, is_disconnected)
    first = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.05)
    bus.publish("pair", {"pair_id": 2, "status": "RUNNING"})
    item = await asyncio.wait_for(first, 1)
    assert item["event"] == "pair"
    assert json.loads(item["data"]) == {"pair_id": 2, "status": "RUNNING"}
    state["gone"] = True
    await gen.aclose()


def test_record_event_persists_and_publishes(db):
    bus = EventBus()
    seen = []
    bus.publish = lambda kind, data: seen.append((kind, data))  # type: ignore[method-assign]
    ev = record_event(db, bus, kind="sync_done", message="Docs sync completed", data={"n": 3})
    assert ev.id is not None
    assert seen[0][0] == "activity"
    assert seen[0][1]["message"] == "Docs sync completed"


def test_activity_api_needs_login_and_lists_newest_first(auth_client, app):
    with app.state.session_factory() as db:
        record_event(db, app.state.bus, kind="a", message="first")
        record_event(db, app.state.bus, kind="b", message="second")
    items = auth_client.get("/api/activity").json()
    assert [i["message"] for i in items][:2] == ["second", "first"]


def test_events_endpoint_requires_login(client):
    assert client.get("/api/events").status_code == 401
