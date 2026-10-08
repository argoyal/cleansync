import asyncio
import time

from app.services.queue import SyncQueue, make_handler


async def test_burst_of_requests_runs_at_most_twice():
    runs = []
    gate = asyncio.Event()

    async def handler(pair_id, action, trigger):
        runs.append((pair_id, action, trigger))
        if len(runs) == 1:
            await gate.wait()

    q = SyncQueue(handler, max_parallel=2)
    assert q.request(1, "MANUAL") == "queued"
    await asyncio.sleep(0.01)
    results = [q.request(1, t) for t in ("WATCHER", "SCHEDULE", "MANUAL", "WATCHER")]
    assert results == ["dirty"] * 4
    gate.set()
    await q.wait_idle()
    assert len(runs) == 2
    assert runs[1][2] == "WATCHER"  # the last trigger wins, it is only a label


async def test_one_pair_never_runs_twice_at_once_and_parallel_limit_holds():
    active, peak = set(), {"n": 0}

    async def handler(pair_id, action, trigger):
        assert pair_id not in active
        active.add(pair_id)
        peak["n"] = max(peak["n"], len(active))
        await asyncio.sleep(0.02)
        active.discard(pair_id)

    q = SyncQueue(handler, max_parallel=2)
    for pid in (1, 2, 3, 4):
        q.request(pid, "SCHEDULE")
    assert q.request(1, "SCHEDULE") == "already_queued"
    await q.wait_idle()
    assert peak["n"] == 2


async def test_non_sync_action_while_running_is_busy():
    gate = asyncio.Event()

    async def handler(pair_id, action, trigger):
        await gate.wait()

    q = SyncQueue(handler)
    q.request(1, "MANUAL")
    await asyncio.sleep(0.01)
    assert q.is_running(1)
    assert q.request(1, "MANUAL", action="preview") == "busy"
    gate.set()
    await q.wait_idle()


async def test_handler_error_does_not_kill_queue():
    calls = []

    async def handler(pair_id, action, trigger):
        calls.append(pair_id)
        if pair_id == 1:
            raise RuntimeError("boom")

    q = SyncQueue(handler)
    q.request(1, "MANUAL")
    q.request(2, "MANUAL")
    await q.wait_idle()
    assert sorted(calls) == [1, 2]


class _Rec:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def f(*args):
            self.calls.append((name, *args))
            return 7

        return f


async def test_handler_dispatches_actions_and_reports_changes():
    engine, initial, changes = _Rec(), _Rec(), []
    handle = make_handler(engine, initial, on_change=lambda: changes.append(1))
    await handle(1, "sync", "MANUAL")
    await handle(1, "preview", "INITIAL")
    await handle(1, "initial", "INITIAL")
    await handle(1, "resync", "MANUAL")
    assert engine.calls == [("run_pair", 1, "MANUAL")]
    assert initial.calls == [
        ("preview", 1),
        ("run_initial", 1),
        ("safe_resync", 1, "MANUAL"),
    ]
    assert len(changes) == 2


async def test_different_non_sync_action_while_pending_is_busy():
    gate = asyncio.Event()

    async def handler(pair_id, action, trigger):
        await gate.wait()

    q = SyncQueue(handler, max_parallel=1)
    q.request(1, "MANUAL")  # occupies the only slot
    await asyncio.sleep(0.01)
    assert q.request(2, "MANUAL") == "queued"  # pending sync
    assert q.request(2, "MANUAL", action="resync") == "busy"
    assert q.request(2, "MANUAL") == "already_queued"
    assert q.request(3, "INITIAL", action="preview") == "queued"  # pending preview
    assert q.request(3, "INITIAL", action="preview") == "already_queued"
    assert q.request(3, "INITIAL", action="initial") == "busy"
    gate.set()
    await q.wait_idle()


async def test_disabled_queue_refuses_everything():
    calls = []

    async def handler(pair_id, action, trigger):
        calls.append(pair_id)

    q = SyncQueue(handler)
    q.disabled_reason = "database integrity check failed"
    assert q.request(1, "MANUAL") == "disabled"
    assert q.request(1, "INITIAL", action="preview") == "disabled"
    await q.wait_idle()
    assert calls == []


async def test_finished_at_is_recorded_after_a_job():
    async def handler(pair_id, action, trigger):
        return None

    q = SyncQueue(handler)
    assert q.finished_at(1) is None
    before = time.monotonic()
    q.request(1, "MANUAL")
    await q.wait_idle()
    finished = q.finished_at(1)
    assert finished is not None and before <= finished <= time.monotonic()
    assert q.finished_at(2) is None


async def test_finish_listeners_hear_about_every_finished_job():
    async def handler(pair_id, action, trigger):
        if pair_id == 2:
            raise RuntimeError("boom")

    heard = []
    q = SyncQueue(handler)
    q.add_finish_listener(heard.append)
    q.request(1, "MANUAL")
    q.request(2, "MANUAL")
    await q.wait_idle()
    assert sorted(heard) == [1, 2]


def test_app_wires_the_watcher_to_finished_jobs(app):
    assert app.state.watcher.job_finished in app.state.queue._finish_listeners
