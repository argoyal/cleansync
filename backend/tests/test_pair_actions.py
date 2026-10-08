import pytest

from app.models import SyncPair
from app.services.sync.initial import CONFIRM_PHRASE


class StubQueue:
    def __init__(self):
        self.requests = []
        self.answer = "queued"

    def request(self, pair_id, trigger, action="sync"):
        self.requests.append((pair_id, trigger, action))
        return self.answer

    def is_running(self, pair_id):
        return False

    async def stop(self):
        pass


@pytest.fixture
def queue(app):
    q = StubQueue()
    app.state.queue = q
    return q


def _pair(auth_client, make_account, **kw):
    acc = make_account()
    body = {"account_id": acc.id, "name": "D"} | kw
    return auth_client.post("/api/sync-pairs", json=body).json()


def _set(db, pair_id, **fields):
    pair = db.get(SyncPair, pair_id)
    for k, v in fields.items():
        setattr(pair, k, v)
    db.commit()


def test_sync_now_requires_initialized_pair(auth_client, make_account, queue):
    p = _pair(auth_client, make_account)
    assert auth_client.post(f"/api/sync-pairs/{p['id']}/sync").status_code == 409
    assert queue.requests == []


def test_sync_now_queues_manual(auth_client, make_account, queue, db):
    p = _pair(auth_client, make_account)
    _set(db, p["id"], initialized=True)
    r = auth_client.post(f"/api/sync-pairs/{p['id']}/sync")
    assert r.status_code == 202 and r.json() == {"result": "queued"}
    assert queue.requests == [(p["id"], "MANUAL", "sync")]


def test_busy_pair_answers_409(auth_client, make_account, queue, db):
    p = _pair(auth_client, make_account)
    _set(db, p["id"], initialized=True)
    queue.answer = "busy"
    assert auth_client.post(f"/api/sync-pairs/{p['id']}/resync").status_code == 409


def test_override_once_sets_flag_and_queues(auth_client, make_account, queue, db):
    p = _pair(auth_client, make_account)
    _set(db, p["id"], initialized=True, attention_reason="BLOCKED")
    r = auth_client.post(f"/api/sync-pairs/{p['id']}/override-once")
    assert r.status_code == 202
    db.expire_all()
    assert db.get(SyncPair, p["id"]).override_next is True
    assert queue.requests[-1] == (p["id"], "MANUAL", "sync")


def test_override_once_without_a_block_is_refused(auth_client, make_account, queue, db):
    p = _pair(auth_client, make_account)
    _set(db, p["id"], initialized=True, attention_reason=None)
    r = auth_client.post(f"/api/sync-pairs/{p['id']}/override-once")
    assert r.status_code == 409 and "nothing is blocked" in r.text
    db.expire_all()
    assert db.get(SyncPair, p["id"]).override_next is False
    assert queue.requests == []


def test_preview_and_confirm_flow(auth_client, make_account, queue, app):
    p = _pair(auth_client, make_account)
    assert auth_client.post(f"/api/sync-pairs/{p['id']}/preview").status_code == 202
    assert queue.requests[-1] == (p["id"], "INITIAL", "preview")
    assert auth_client.get(f"/api/sync-pairs/{p['id']}/preview").status_code == 404
    app.state.initial.load_preview = lambda pid: {"job_id": 1, "different": 0}
    assert auth_client.get(f"/api/sync-pairs/{p['id']}/preview").json()["job_id"] == 1
    bad = auth_client.post(f"/api/sync-pairs/{p['id']}/confirm-initial", json={"phrase": "yes"})
    assert bad.status_code == 422
    ok = auth_client.post(
        f"/api/sync-pairs/{p['id']}/confirm-initial", json={"phrase": CONFIRM_PHRASE}
    )
    assert ok.status_code == 202
    assert queue.requests[-1] == (p["id"], "INITIAL", "initial")


def test_preview_of_initialized_pair_is_refused(auth_client, make_account, queue, db):
    p = _pair(auth_client, make_account)
    _set(db, p["id"], initialized=True)
    assert auth_client.post(f"/api/sync-pairs/{p['id']}/preview").status_code == 409
    assert queue.requests == []


def test_confirm_without_preview_is_refused(auth_client, make_account, queue):
    p = _pair(auth_client, make_account)
    r = auth_client.post(
        f"/api/sync-pairs/{p['id']}/confirm-initial", json={"phrase": CONFIRM_PHRASE}
    )
    assert r.status_code == 409


def test_resync_button_queues_resync(auth_client, make_account, queue, db):
    p = _pair(auth_client, make_account)
    _set(db, p["id"], initialized=True)
    assert auth_client.post(f"/api/sync-pairs/{p['id']}/resync").status_code == 202
    assert queue.requests[-1] == (p["id"], "MANUAL", "resync")


def test_resync_of_one_way_pair_is_refused(auth_client, make_account, queue, db):
    p = _pair(auth_client, make_account, direction="REMOTE_TO_LOCAL")
    _set(db, p["id"], initialized=True)
    r = auth_client.post(f"/api/sync-pairs/{p['id']}/resync")
    assert r.status_code == 409 and "one-way pairs don't need a resync" in r.text
    assert queue.requests == []


def test_actions_on_missing_pair_are_404(auth_client, queue):
    assert auth_client.post("/api/sync-pairs/999/sync").status_code == 404


def test_app_exposes_queue_and_scheduler(settings):
    from app.main import create_app
    from app.services.queue import SyncQueue
    from app.services.scheduler import Scheduler

    a = create_app(settings, start_background=False)
    assert isinstance(a.state.queue, SyncQueue)
    assert isinstance(a.state.scheduler, Scheduler)


def test_routes_queue_on_the_real_event_loop(auth_client, make_account, db, app):
    # No stub: the real SyncQueue needs the event loop thread, so the route must be async.
    p = _pair(auth_client, make_account, direction="REMOTE_TO_LOCAL")
    _set(db, p["id"], initialized=True)
    seen = []

    async def handler(pair_id, action, trigger):
        seen.append((pair_id, action, trigger))

    from app.services.queue import SyncQueue

    app.state.queue = SyncQueue(handler)
    r = auth_client.post(f"/api/sync-pairs/{p['id']}/sync")
    assert r.status_code == 202, r.text
    assert r.json() == {"result": "queued"}


def test_pending_sync_blocks_a_resync_request(auth_client, make_account, queue, db):
    p = _pair(auth_client, make_account)
    _set(db, p["id"], initialized=True)
    queue.answer = "busy"  # what SyncQueue answers when a different action is pending
    r = auth_client.post(f"/api/sync-pairs/{p['id']}/resync")
    assert r.status_code == 409 and "busy" in r.text


def test_db_integrity_failure_disables_the_queue(settings, monkeypatch):
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "integrity_ok", lambda engine: False)
    a = main_mod.create_app(settings, start_background=False)
    assert a.state.queue.request(1, "MANUAL") == "disabled"
    assert a.state.queue.request(1, "INITIAL", action="preview") == "disabled"


def test_disabled_queue_answers_503(auth_client, make_account, db, app):
    p = _pair(auth_client, make_account)
    _set(db, p["id"], initialized=True)
    app.state.queue.disabled_reason = "database integrity check failed"
    for path in ("sync", "resync", "override-once"):
        if path == "override-once":
            _set(db, p["id"], attention_reason="BLOCKED")
        r = auth_client.post(f"/api/sync-pairs/{p['id']}/{path}")
        assert r.status_code == 503, path
        assert "integrity" in r.text and "backup-recovery.md" in r.text


def test_refused_override_does_not_leave_the_flag_set(auth_client, make_account, db, app):
    p = _pair(auth_client, make_account)
    _set(db, p["id"], initialized=True, attention_reason="BLOCKED")
    app.state.queue.disabled_reason = "database integrity check failed"
    assert auth_client.post(f"/api/sync-pairs/{p['id']}/override-once").status_code == 503
    db.expire_all()
    assert db.get(SyncPair, p["id"]).override_next is False
