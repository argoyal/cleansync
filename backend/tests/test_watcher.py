import asyncio
import time

import pytest

from app.services.watcher import Debouncer, LocalWatcher, self_test, should_ignore


class StubQueue:
    def __init__(self, running=()):
        self.requests = []
        self.running = set(running)
        self.finished = None

    def request(self, pair_id, trigger, action="sync"):
        self.requests.append((pair_id, trigger))
        return "queued"

    def is_running(self, pair_id):
        return pair_id in self.running

    def finished_at(self, pair_id):
        return self.finished


async def test_debouncer_coalesces_bursts():
    fired = []
    d = Debouncer(0.05, fired.append, asyncio.get_running_loop())
    for _ in range(5):
        d.touch(1)
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)
    assert fired == [1]


@pytest.mark.parametrize(
    "path,ignored",
    [
        ("RCLONE_TEST", True),
        ("sub/RCLONE_TEST", True),
        (".google-exports/a.docx", True),
        ("video.mp4.partial", True),
        ("report.xlsx", False),
        ("a/b/c.txt", False),
    ],
)
def test_should_ignore(path, ignored):
    assert should_ignore(path) is ignored


async def _watch(session_factory, layout, queue):
    w = LocalWatcher(session_factory=session_factory, layout=layout, queue=queue, debounce_s=1.0)
    w.start(asyncio.get_running_loop())
    await asyncio.sleep(0.3)
    return w


async def test_local_change_queues_one_sync(session_factory, layout, make_account, make_pair):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    q = StubQueue()
    w = await _watch(session_factory, layout, q)
    try:
        root = layout.account_dir(acc.email)
        for i in range(5):
            (root / f"f{i}.txt").write_text("x")
        await asyncio.sleep(4.0)  # macOS FSEvents can take ~1 s to deliver
        assert q.requests == [(pair.id, "WATCHER")]
    finally:
        w.stop()


async def test_ignored_paths_and_running_pairs_do_not_queue(
    session_factory, layout, make_account, make_pair
):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    q = StubQueue(running={pair.id})
    w = await _watch(session_factory, layout, q)
    try:
        root = layout.account_dir(acc.email)
        (root / "RCLONE_TEST").write_text("x")
        (root / "real.txt").write_text("x")
        await asyncio.sleep(4.0)
        assert q.requests == []
    finally:
        w.stop()


async def test_self_test_detects_events(tmp_path):
    assert await self_test(tmp_path) is True


async def test_observer_failure_sets_limited_not_raise(
    session_factory, layout, make_account, make_pair, monkeypatch
):
    acc = make_account()
    make_pair(acc, initialized=True)
    w = LocalWatcher(session_factory=session_factory, layout=layout, queue=StubQueue())

    def boom(*a, **k):
        raise OSError("inotify watch limit reached")

    monkeypatch.setattr("app.services.watcher.Observer.schedule", boom)
    w.start(asyncio.get_running_loop())
    try:
        assert w.status == "limited"
    finally:
        w.stop()


async def test_events_right_after_a_job_are_ignored_but_later_ones_queue(
    session_factory, layout, make_account, make_pair
):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    q = StubQueue()
    w = LocalWatcher(session_factory=session_factory, layout=layout, queue=q)
    q.finished = time.monotonic() - 0.5
    w._fire(pair.id)
    assert q.requests == []
    q.finished = time.monotonic() - 2.5
    w._fire(pair.id)
    assert q.requests == [(pair.id, "WATCHER")]


async def test_ignored_files_and_dir_events_do_not_queue_but_real_file_does(
    session_factory, layout, make_account, make_pair
):
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    q = StubQueue()
    w = await _watch(session_factory, layout, q)
    try:
        root = layout.account_dir(acc.email)
        (root / "RCLONE_TEST").write_text("x")
        (root / "movie.mp4.partial").write_text("x")
        await asyncio.sleep(4.0)
        assert q.requests == []
        (root / "real.txt").write_text("x")
        await asyncio.sleep(4.0)
        assert q.requests == [(pair.id, "WATCHER")]
    finally:
        w.stop()


async def test_reload_with_unchanged_pairs_keeps_watches(
    session_factory, layout, make_account, make_pair, monkeypatch
):
    acc = make_account()
    make_pair(acc, initialized=True)
    w = await _watch(session_factory, layout, StubQueue())
    try:
        calls = []
        monkeypatch.setattr(w._observer, "schedule", lambda *a, **k: calls.append("s"))
        monkeypatch.setattr(w._observer, "unschedule", lambda *a, **k: calls.append("u"))
        monkeypatch.setattr(w._observer, "unschedule_all", lambda *a, **k: calls.append("ua"))
        w.reload()
        assert calls == []
        assert w.status == "active"
    finally:
        w.stop()


async def test_one_failing_root_is_isolated_and_status_recovers(
    session_factory, layout, make_account, make_pair, monkeypatch
):
    acc1 = make_account()
    acc2 = make_account(email="bob@example.com")
    p1 = make_pair(acc1, initialized=True)
    p2 = make_pair(acc2, initialized=True)
    bad = str(layout.account_dir(acc1.email))
    w = LocalWatcher(session_factory=session_factory, layout=layout, queue=StubQueue())
    from app.services import watcher as mod

    orig = mod.Observer.schedule
    state = {"fail": True}

    def flaky(self, handler, path, **kw):
        if state["fail"] and path == bad:
            raise OSError("watch limit")
        return orig(self, handler, path, **kw)

    monkeypatch.setattr(mod.Observer, "schedule", flaky)
    w.start(asyncio.get_running_loop())
    try:
        assert w.status == "limited"
        assert set(w._roots.values()) == {p2.id}
        state["fail"] = False
        w.reload()
        assert w.status == "active"
        assert set(w._roots.values()) == {p1.id, p2.id}
        w.events_ok = False
        w.reload()
        assert w.status == "limited"
    finally:
        w.stop()


async def test_debouncer_cancel_drops_one_pending_timer():
    fired = []
    d = Debouncer(0.05, fired.append, asyncio.get_running_loop())
    d.touch(1)
    d.touch(2)
    d.cancel(1)
    await asyncio.sleep(0.1)
    assert fired == [2]


async def test_finished_job_cancels_the_pairs_pending_change(
    session_factory, layout, make_account, make_pair
):
    # M1: rclone's own writes during a run must not queue a follow-up sync once it ends.
    acc = make_account()
    pair = make_pair(acc, initialized=True)
    q = StubQueue()
    w = LocalWatcher(session_factory=session_factory, layout=layout, queue=q, debounce_s=0.1)
    # No observer: drive the debouncer directly, so stray file events can't interfere.
    w._loop = asyncio.get_running_loop()
    w._debouncer = Debouncer(0.1, w._fire, w._loop)
    w._debouncer.touch(pair.id)
    w._debouncer.touch(pair.id + 1)
    w.job_finished(pair.id)
    await asyncio.sleep(0.3)
    assert q.requests == [(pair.id + 1, "WATCHER")]
