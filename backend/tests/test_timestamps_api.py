"""I7: every timestamp the API returns says it is UTC."""

import re
from datetime import datetime

from app.models import Conflict, Event, SyncJob

_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T")


def _timestamps(value, found):
    if isinstance(value, dict):
        for v in value.values():
            _timestamps(v, found)
    elif isinstance(value, list):
        for v in value:
            _timestamps(v, found)
    elif isinstance(value, str) and _TS.match(value):
        found.append(value)
    return found


def test_every_api_timestamp_is_utc(auth_client, db, make_account, make_pair):
    naive = datetime(2026, 10, 8, 9, 30)
    acc = make_account()
    acc.last_checked_at = naive
    pair = make_pair(
        acc,
        initialized=True,
        schedule_interval_s=300,
        last_sync_at=naive,
        last_success_at=naive,
        last_error_at=naive,
    )
    db.add(
        SyncJob(
            pair_id=pair.id,
            kind="BISYNC",
            trigger="MANUAL",
            state="SUCCESS",
            started_at=naive,
            completed_at=naive,
        )
    )
    db.add(
        Conflict(
            pair_id=pair.id,
            path="a",
            local_variant="a.conflict1",
            remote_variant="a.conflict2",
            local_mtime=naive,
            remote_mtime=naive,
        )
    )
    db.add(Event(kind="test", message="hello", pair_id=pair.id, ts=naive))
    db.commit()
    found: list[str] = []
    for url in (
        f"/api/sync-pairs/{pair.id}",
        "/api/sync-pairs",
        "/api/accounts",
        f"/api/sync-pairs/{pair.id}/history",
        f"/api/sync-pairs/{pair.id}/conflicts",
        "/api/activity",
    ):
        r = auth_client.get(url)
        assert r.status_code == 200, (url, r.text)
        _timestamps(r.json(), found)
    assert len(found) >= 12
    assert all(ts.endswith(("Z", "+00:00")) for ts in found), found
