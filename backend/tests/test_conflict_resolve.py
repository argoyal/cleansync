from datetime import date

import pytest

from app.models import Conflict
from app.services.sync.conflicts import ConflictError, resolve_conflict


@pytest.fixture
def setup(db, layout, make_account, make_pair):
    acc = make_account()
    pair = make_pair(acc, initialized=True, status="ATTENTION", attention_reason="CONFLICTS")
    root = layout.account_dir(acc.email)
    (root / "d").mkdir()
    (root / "d" / "r.conflict1.xlsx").write_text("LOCAL")
    (root / "d" / "r.conflict2.xlsx").write_text("DRIVE")
    c = Conflict(
        pair_id=pair.id,
        path="d/r.xlsx",
        local_variant="d/r.conflict1.xlsx",
        remote_variant="d/r.conflict2.xlsx",
    )
    db.add(c)
    db.commit()
    return acc, pair, c, root


def _resolve(db, layout, setup, action):
    acc, pair, c, _ = setup
    return resolve_conflict(db, layout, acc, pair, c, action, date(2026, 10, 8))


def test_keep_local_moves_drive_version_to_backups(db, layout, setup):
    acc, pair, c, root = setup
    _resolve(db, layout, setup, "keep_local")
    assert (root / "d" / "r.xlsx").read_text() == "LOCAL"
    assert not (root / "d" / "r.conflict2.xlsx").exists()
    saved = (
        layout.backups_dir
        / acc.email
        / str(pair.id)
        / "2026-10-08"
        / "conflicts"
        / "d"
        / "r.conflict2.xlsx"
    )
    assert saved.read_text() == "DRIVE"
    db.refresh(pair)
    assert c.state == "RESOLVED" and pair.status == "HEALTHY"


def test_keep_remote(db, layout, setup):
    _, _, _, root = setup
    _resolve(db, layout, setup, "keep_remote")
    assert (root / "d" / "r.xlsx").read_text() == "DRIVE"


def test_keep_both_renames_clearly(db, layout, setup):
    _, _, _, root = setup
    _resolve(db, layout, setup, "keep_both")
    assert (root / "d" / "r (local).xlsx").read_text() == "LOCAL"
    assert (root / "d" / "r (Drive).xlsx").read_text() == "DRIVE"


def test_skip_leaves_files(db, layout, setup):
    _, _, c, root = setup
    _resolve(db, layout, setup, "skip")
    assert c.state == "SKIPPED"
    assert (root / "d" / "r.conflict1.xlsx").exists()


def test_existing_original_is_backed_up_not_overwritten(db, layout, setup):
    acc, pair, c, root = setup
    (root / "d" / "r.xlsx").write_text("SURPRISE")
    _resolve(db, layout, setup, "keep_local")
    saved = (
        layout.backups_dir / acc.email / str(pair.id) / "2026-10-08" / "conflicts" / "d" / "r.xlsx"
    )
    assert saved.read_text() == "SURPRISE"


def test_missing_variant_files_raise(db, layout, setup):
    _, _, _, root = setup
    (root / "d" / "r.conflict2.xlsx").unlink()
    with pytest.raises(ConflictError):
        _resolve(db, layout, setup, "keep_local")


def test_resolve_api_queues_sync(auth_client, app, db, layout, setup):
    _, pair, c, _ = setup

    class Q:
        requests = []

        def request(self, *a, **k):
            self.requests.append(a)
            return "queued"

        def is_running(self, pair_id):
            return False

        async def stop(self):
            pass

    app.state.queue = Q()
    r = auth_client.post(f"/api/conflicts/{c.id}/resolve", json={"action": "keep_local"})
    assert r.status_code == 200 and r.json()["state"] == "RESOLVED"
    assert Q.requests == [(pair.id, "RESOLVE")]
    assert (
        auth_client.post(f"/api/conflicts/{c.id}/resolve", json={"action": "nonsense"}).status_code
        == 422
    )


def test_resolve_api_refuses_before_moving_files_when_disabled(auth_client, app, db, setup):
    _, _, c, root = setup
    app.state.queue.disabled_reason = "database integrity check failed"
    r = auth_client.post(f"/api/conflicts/{c.id}/resolve", json={"action": "keep_local"})
    assert r.status_code == 503
    assert (root / "d" / "r.conflict1.xlsx").exists()
    assert (root / "d" / "r.conflict2.xlsx").exists()
    db.refresh(c)
    assert c.state == "OPEN"


def test_resolve_api_busy_still_resolves_and_says_sync_is_later(auth_client, app, setup):
    _, _, c, root = setup

    class Busy:
        def request(self, *a, **k):
            return "busy"

        def is_running(self, pair_id):
            return False

        async def stop(self):
            pass

    app.state.queue = Busy()
    r = auth_client.post(f"/api/conflicts/{c.id}/resolve", json={"action": "keep_local"})
    assert r.status_code == 200
    assert r.json()["state"] == "RESOLVED" and r.json()["sync"] == "busy"
    assert (root / "d" / "r.xlsx").read_text() == "LOCAL"


def test_resolve_api_conflict_error_is_409(auth_client, app, setup):
    _, _, c, root = setup
    (root / "d" / "r.conflict2.xlsx").unlink()
    r = auth_client.post(f"/api/conflicts/{c.id}/resolve", json={"action": "keep_local"})
    assert r.status_code == 409


def test_list_conflicts_defaults_to_open(auth_client, setup):
    _, pair, c, _ = setup
    body = auth_client.get(f"/api/sync-pairs/{pair.id}/conflicts").json()
    assert [x["id"] for x in body] == [c.id]
    assert auth_client.get(f"/api/sync-pairs/{pair.id}/conflicts?state=RESOLVED").json() == []


def _again(db, pair, root, name_l="r.conflict1.xlsx", name_r="r.conflict2.xlsx"):
    (root / "d" / name_l).write_text("LOCAL2")
    (root / "d" / name_r).write_text("DRIVE2")
    c = Conflict(
        pair_id=pair.id, path="d/r.xlsx", local_variant="d/" + name_l, remote_variant="d/" + name_r
    )
    db.add(c)
    db.commit()
    return c


def test_second_resolve_same_day_keeps_every_backup(db, layout, setup):
    acc, pair, c, root = setup
    _resolve(db, layout, setup, "keep_local")
    c2 = _again(db, pair, root)
    resolve_conflict(db, layout, acc, pair, c2, "keep_local", date(2026, 10, 8))
    folder = layout.backups_dir / acc.email / str(pair.id) / "2026-10-08" / "conflicts" / "d"
    contents = sorted(f.read_text() for f in folder.iterdir())
    assert contents == ["DRIVE", "DRIVE2", "LOCAL"]  # first original (LOCAL) backed up too


def test_keep_remote_saves_local_loser(db, layout, setup):
    acc, pair, _, root = setup
    _resolve(db, layout, setup, "keep_remote")
    saved = layout.backups_dir / acc.email / str(pair.id) / "2026-10-08" / "conflicts" / "d"
    assert (saved / "r.conflict1.xlsx").read_text() == "LOCAL"


def test_keep_both_with_existing_name_does_not_overwrite(db, layout, setup):
    _, _, _, root = setup
    (root / "d" / "r (local).xlsx").write_text("OLD")
    _resolve(db, layout, setup, "keep_both")
    assert (root / "d" / "r (local).xlsx").read_text() == "OLD"
    assert (root / "d" / "r (local) 2.xlsx").read_text() == "LOCAL"


def test_resolving_handled_conflict_again_is_409(auth_client, setup):
    _, _, c, _ = setup
    assert (
        auth_client.post(f"/api/conflicts/{c.id}/resolve", json={"action": "skip"}).status_code
        == 200
    )
    r = auth_client.post(f"/api/conflicts/{c.id}/resolve", json={"action": "keep_local"})
    assert r.status_code == 409


def test_unsafe_stored_variant_is_409_and_moves_nothing(auth_client, db, setup):
    _, _, c, root = setup
    c.remote_variant = "../x"
    db.commit()
    r = auth_client.post(f"/api/conflicts/{c.id}/resolve", json={"action": "keep_local"})
    assert r.status_code == 409
    assert (root / "d" / "r.conflict1.xlsx").exists()
    assert not (root / "d" / "r.xlsx").exists()


def test_resolve_api_409_while_pair_is_syncing(auth_client, app, setup):
    _, _, c, root = setup

    class Running:
        def request(self, *a, **k):
            raise AssertionError("must not queue")

        def is_running(self, pair_id):
            return True

        async def stop(self):
            pass

    app.state.queue = Running()
    r = auth_client.post(f"/api/conflicts/{c.id}/resolve", json={"action": "keep_local"})
    assert r.status_code == 409 and "busy" in r.json()["detail"]
    assert (root / "d" / "r.conflict1.xlsx").exists()


def test_bad_state_filter_is_422(auth_client, setup):
    _, pair, _, _ = setup
    assert auth_client.get(f"/api/sync-pairs/{pair.id}/conflicts?state=bogus").status_code == 422
