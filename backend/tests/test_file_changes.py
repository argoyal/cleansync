"""Follow-ups of file actions are coalesced: a folder upload of many files makes one "files"
event per window and one sync after the burst, not one of each per file."""

import asyncio
import time

import pytest

from app.services.files.changes import FileChanges


class _Bus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, kind, data):
        self.events.append((kind, data))


class _Queue:
    def __init__(self):
        self.requests: list[tuple[int, str]] = []

    def request(self, pair_id, trigger, action="sync"):
        self.requests.append((pair_id, str(trigger)))
        return "queued"


class _Indexes:
    def __init__(self):
        self.invalidated: list[int] = []

    def invalidate(self, pair_id=None):
        self.invalidated.append(pair_id)


@pytest.fixture
def parts():
    bus, queue, indexes = _Bus(), _Queue(), _Indexes()
    changes = FileChanges(
        bus=bus, get_queue=lambda: queue, indexes=indexes, sync_delay_s=0.2, event_window_s=0.2
    )
    return changes, bus, queue, indexes


async def test_files_events_are_coalesced_per_account(parts):
    changes, bus, _, _ = parts
    for _ in range(20):
        changes.notify(1)
    changes.notify(2)
    # The first one goes out at once; the rest of the burst becomes one more event.
    assert bus.events == [("files", {"account_id": 1}), ("files", {"account_id": 2})]
    await asyncio.sleep(0.35)
    assert bus.events.count(("files", {"account_id": 1})) == 2
    assert bus.events.count(("files", {"account_id": 2})) == 1


async def test_one_app_sync_after_a_burst(parts):
    changes, _, queue, indexes = parts
    answers = [changes.request_sync(7, initialized=True) for _ in range(30)]
    assert set(answers) == {"scheduled"}
    await asyncio.sleep(0.1)
    changes.request_sync(7, initialized=True)  # still going: wait again
    await asyncio.sleep(0.15)
    assert queue.requests == [] and indexes.invalidated == []
    await asyncio.sleep(0.2)
    assert queue.requests == [(7, "APP")]
    assert indexes.invalidated == [7]  # once, when the burst settled


async def test_uninitialized_pairs_only_refresh_statuses(parts):
    changes, _, queue, indexes = parts
    assert changes.request_sync(3, initialized=False) == "not_initialized"
    await asyncio.sleep(0.3)
    assert queue.requests == [] and indexes.invalidated == [3]


async def test_without_a_delay_the_sync_is_asked_for_at_once():
    queue, indexes = _Queue(), _Indexes()
    changes = FileChanges(bus=_Bus(), get_queue=lambda: queue, indexes=indexes, sync_delay_s=0)
    assert changes.request_sync(4, initialized=True) == "queued"
    assert queue.requests == [(4, "APP")]


async def test_stop_drops_pending_work(parts):
    changes, bus, queue, _ = parts
    changes.notify(1)
    changes.notify(1)
    changes.request_sync(5, initialized=True)
    changes.stop()
    await asyncio.sleep(0.3)
    assert queue.requests == [] and len(bus.events) == 1


def test_folder_upload_makes_one_sync_and_keeps_the_index(
    auth_client, app, make_account, make_pair, stub_queue
):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    app.state.file_changes.sync_delay_s = 0.3
    gen = dict(app.state.indexes._gen)
    for n in range(5):
        r = auth_client.post(
            "/api/files/upload",
            params={"account_id": acc.id, "path": "", "name": f"Trip/day {n}.jpg"},
            content=b"x",
        )
        assert r.status_code == 200, r.text
        assert r.json()["syncs"] == {str(pair.id): "scheduled"}
    assert stub_queue.requests == []
    assert app.state.indexes._gen == gen  # listing judges new files on the spot
    deadline = time.monotonic() + 3
    while not stub_queue.requests and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.4)
    assert stub_queue.requests == [(pair.id, "APP", "sync")]
