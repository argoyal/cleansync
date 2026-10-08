import asyncio

import pytest

from app.services.watcher import Debouncer, LocalWatcher, self_test, should_ignore


class StubQueue:
    def __init__(self, running=()):
        self.requests = []
        self.running = set(running)

    def request(self, pair_id, trigger, action="sync"):
        self.requests.append((pair_id, trigger))
        return "queued"

    def is_running(self, pair_id):
        return pair_id in self.running

    def finished_at(self, pair_id):
        return None


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
