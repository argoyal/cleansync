from app.models import PlannedChange, SyncJob


def _pair_with_jobs(db, make_account, make_pair, layout):
    acc = make_account()
    pair = make_pair(acc, initialized=True, status="HEALTHY")
    dry = SyncJob(
        pair_id=pair.id, kind="DRY_RUN", trigger="SCHEDULE", state="BLOCKED", planned_deletions=2
    )
    real = SyncJob(
        pair_id=pair.id, kind="BISYNC", trigger="MANUAL", state="SUCCESS", files_uploaded=3
    )
    db.add_all([dry, real])
    db.commit()
    db.add_all(
        [
            PlannedChange(job_id=dry.id, side="remote", action="delete", path="a.txt"),
            PlannedChange(job_id=dry.id, side="local", action="delete", path="b.txt"),
        ]
    )
    real.log_path = str(layout.job_log(real.id))
    layout.job_log(real.id).write_text("one\ntwo\nthree\n")
    db.commit()
    return pair, dry, real


def test_status_counts(auth_client, db, make_account, make_pair):
    acc = make_account()
    make_pair(acc, status="HEALTHY")
    make_pair(acc, status="ATTENTION", local_subpath="b", remote_path="b")
    make_pair(acc, status="ERROR", local_subpath="c", remote_path="c")
    s = auth_client.get("/api/status").json()
    assert (s["accounts"], s["pairs"], s["healthy"], s["attention"]) == (1, 3, 1, 2)


def test_history_hides_dry_runs_by_default(auth_client, db, make_account, make_pair, layout):
    pair, dry, real = _pair_with_jobs(db, make_account, make_pair, layout)
    items = auth_client.get(f"/api/sync-pairs/{pair.id}/history").json()
    assert [i["id"] for i in items] == [real.id]
    all_items = auth_client.get(f"/api/sync-pairs/{pair.id}/history?include_dry_runs=true").json()
    assert [i["id"] for i in all_items] == [real.id, dry.id]


def test_logs_tail_and_download(auth_client, db, make_account, make_pair, layout):
    pair, _, real = _pair_with_jobs(db, make_account, make_pair, layout)
    body = auth_client.get(f"/api/sync-pairs/{pair.id}/logs?tail=2").json()
    assert body == {"job_id": real.id, "lines": ["two", "three"]}
    dl = auth_client.get(f"/api/sync-pairs/{pair.id}/logs/{real.id}/download")
    assert dl.status_code == 200 and dl.text == "one\ntwo\nthree\n"
    assert "attachment" in dl.headers["content-disposition"]


def test_log_download_for_other_pairs_job_is_404(auth_client, db, make_account, make_pair, layout):
    pair, _, real = _pair_with_jobs(db, make_account, make_pair, layout)
    acc2 = make_account("bob@example.com")
    other = make_pair(acc2)
    assert auth_client.get(f"/api/sync-pairs/{other.id}/logs/{real.id}/download").status_code == 404


def test_planned_changes_default_to_latest_blocked(
    auth_client, db, make_account, make_pair, layout
):
    pair, dry, _ = _pair_with_jobs(db, make_account, make_pair, layout)
    body = auth_client.get(f"/api/sync-pairs/{pair.id}/planned-changes").json()
    assert body["job_id"] == dry.id
    assert sorted((c["side"], c["path"]) for c in body["changes"]) == [
        ("local", "b.txt"),
        ("remote", "a.txt"),
    ]


def test_logs_for_pair_without_jobs(auth_client, make_account, make_pair):
    pair = make_pair(make_account())
    assert auth_client.get(f"/api/sync-pairs/{pair.id}/logs").json() == {
        "job_id": None,
        "lines": [],
    }


def test_status_counts_new(auth_client, make_account, make_pair):
    make_pair(make_account())
    assert auth_client.get("/api/status").json()["new"] == 1
