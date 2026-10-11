from datetime import UTC, datetime, timedelta

import pytest

from app.devtools.fake_gmail import FakeGmail, make_mail
from app.models import MailJob
from app.models.enums import JobState, MailJobKind, ServiceKind, ServiceState
from app.services.accounts.services import ensure_services, service_state, set_service_state
from app.services.mail.jobs import finish_mail_job, start_mail_job
from app.services.queue import MISSING_DISK_MSG
from tests.mail_helpers import fake_adapter


class OneAdapter:
    def __init__(self, adapter):
        self.adapter = adapter

    def for_account(self, account):
        return self.adapter


class StubMailQueue:
    def __init__(self):
        self.requests = []

    def request(self, account_id, action, args=None, trigger="SCHEDULE"):
        self.requests.append((account_id, action, args or {}, trigger))
        return "queued"

    def is_busy(self, account_id):
        return False

    def is_running(self, account_id):
        return False

    async def stop(self):
        pass


@pytest.fixture
def mail_app(app, make_account, db, stub_queue):
    acc = make_account()
    ensure_services(db, acc)
    fake = FakeGmail(acc.email)
    when = datetime(2026, 9, 1, tzinfo=UTC)
    for i in range(3):
        fake.add(make_mail(f"Mail {i}", when=when), when=when)
    app.state.mail.adapters = OneAdapter(fake_adapter(fake))
    app.state.mail.queue = StubMailQueue()

    async def size(_account_id):
        return 6_200_000_000

    app.state.accounts.storage_outside_drive = size
    return app, acc, fake


def test_setup_info_counts_the_mail_and_start_queues_the_first_backup(auth_client, mail_app, db):
    app, acc, _ = mail_app
    assert auth_client.post("/api/mail/start", json={"account_id": acc.id}).status_code == 409
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.SETTING_UP)
    info = auth_client.get(f"/api/mail/setup-info?account_id={acc.id}").json()
    assert info == {"total": 3, "size_bytes": 6_200_000_000}
    r = auth_client.post("/api/mail/start", json={"account_id": acc.id})
    assert r.status_code == 202 and r.json() == {"queued": "queued"}
    db.expire_all()
    assert service_state(db, acc.id, ServiceKind.MAIL) == ServiceState.ACTIVE
    assert app.state.mail.queue.requests == [(acc.id, "backup", {}, "SETUP")]


def test_check_now_and_its_refusals(auth_client, mail_app, db):
    app, acc, _ = mail_app
    r = auth_client.post("/api/mail/check", json={"account_id": acc.id})
    assert r.status_code == 409 and "isn't set up" in r.json()["detail"]
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    assert auth_client.post("/api/mail/check", json={"account_id": acc.id}).status_code == 202
    assert app.state.mail.queue.requests[-1] == (acc.id, "backup", {}, "MANUAL")
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.NEEDS_RECONNECT)
    r = auth_client.post("/api/mail/check", json={"account_id": acc.id})
    assert r.status_code == 409 and "Reconnect" in r.json()["detail"]
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    app.state.queue.disabled_reason = MISSING_DISK_MSG
    r = auth_client.post("/api/mail/check", json={"account_id": acc.id})
    assert r.status_code == 503 and r.json()["detail"] == MISSING_DISK_MSG


async def test_a_backup_job_runs_and_the_status_shows_it(auth_client, mail_app, db):
    app, acc, _ = mail_app
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    await app.state.mail.handle(acc.id, "backup", {}, "MANUAL")
    s = auth_client.get(f"/api/mail/status?account_id={acc.id}").json()
    assert s["service"] == "active" and s["phase"] == "incremental"
    assert s["stored"] == 3 and s["stored_bytes"] > 0 and s["total"] == 3
    assert s["job"]["kind"] == "BACKUP" and s["job"]["state"] == "SUCCESS"
    assert s["job"]["message"] == "Saved 3 new mails"
    assert s["failed"] == [] and s["last_error"] is None and s["budget_reached"] is False
    events = auth_client.get("/api/activity").json()
    assert any(
        "Mail backup for alice@example.com: Saved 3 new mails" in e["message"] for e in events
    )
    log = auth_client.get(f"/api/mail/jobs/{s['job']['id']}/log")
    assert log.status_code == 200 and "saved" in log.text


async def test_a_dead_login_marks_the_whole_account(mail_app, db):
    app, acc, fake = mail_app
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    fake.revoke()
    await app.state.mail.handle(acc.id, "backup", {}, "SCHEDULE")
    db.expire_all()
    from app.models import Account

    assert db.get(Account, acc.id).status == "NEEDS_REAUTH"
    assert service_state(db, acc.id, ServiceKind.MAIL) == ServiceState.NEEDS_RECONNECT


async def test_the_tick_checks_each_account_when_its_interval_has_passed(mail_app, db):
    app, acc, _ = mail_app
    mail = app.state.mail
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    await mail.tick()
    assert mail.queue.requests == [(acc.id, "backup", {}, "SCHEDULE")]  # never run yet
    from app.services.mail.index import sync_state

    st = sync_state(db, acc.id)
    st.last_run_at = datetime.now(UTC) - timedelta(seconds=60)
    db.commit()
    await mail.tick()
    assert len(mail.queue.requests) == 1  # 5 minutes have not passed
    app.state.budget.spend(app.state.budget.budget())
    st.last_run_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()
    await mail.tick()
    assert len(mail.queue.requests) == 1  # the budget is used up: wait for the next UTC day


def test_sync_activity_lists_drive_and_mail_jobs_together(
    auth_client, app, db, make_account, make_pair
):
    acc = make_account()
    pair = make_pair(acc, initialized=True, name="My Drive")
    sync = app.state.engine.start_job(db, pair, "BISYNC", "MANUAL")
    app.state.engine.finish_job(db, sync, "SUCCESS")
    job = start_mail_job(db, app.state.layout, app.state.bus, acc.id, MailJobKind.CHECK, "SCHEDULE")
    finish_mail_job(db, app.state.bus, job.id, JobState.PAUSED, "Paused: less than 2 GB free")
    rows = auth_client.get("/api/activity/jobs").json()
    assert [r["source"] for r in rows] == ["mail", "files"]
    assert rows[0]["title"] == "Mail: alice@example.com"
    assert rows[0]["message"] == "Paused: less than 2 GB free"
    assert rows[0]["log_url"] == f"/api/mail/jobs/{job.id}/log"
    assert rows[1]["title"] == "My Drive"
    assert rows[1]["log_url"] == f"/api/sync-pairs/{pair.id}/logs/{sync.id}/download"
    assert db.get(MailJob, job.id).state == JobState.PAUSED


async def test_storage_outside_drive_reads_rclone_about(app, make_account, fake_rclone):
    known = make_account("alice@example.com")
    unknown = make_account("bob@example.com")
    broken = make_account("carol@example.com")
    fake_rclone.on("about", match=["gd_alice:"], stdout=['{"total": 2000, "other": 6200}'])
    fake_rclone.on("about", match=["gd_bob:"], stdout=['{"total": 2000, "used": 50}'])
    fake_rclone.on("about", match=["gd_carol:"], stderr=["CRITICAL: no token"], exit=1)
    accounts = app.state.accounts
    assert await accounts.storage_outside_drive(known.id) == 6200
    assert await accounts.storage_outside_drive(unknown.id) is None
    assert await accounts.storage_outside_drive(broken.id) is None
    assert await accounts.storage_outside_drive(9999) is None


async def test_stopping_mail_saves_the_units_counted_today(app, db):
    from app.models import ApiUsage
    from app.services.google.quota import COUNTED

    app.state.budget.spend(7)
    assert db.get(ApiUsage, (app.state.budget.day(), COUNTED)) is None  # not saved yet
    await app.state.mail.stop()
    assert db.get(ApiUsage, (app.state.budget.day(), COUNTED)).units == 7


def test_a_mail_job_left_running_is_closed_and_queued_again_at_startup(settings, make_account, db):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.mail.jobs import INTERRUPTED_MAIL_MSG

    first = create_app(settings, start_background=False)
    acc = make_account()
    ensure_services(db, acc)
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    job = start_mail_job(
        db, first.state.layout, first.state.bus, acc.id, MailJobKind.CHECK, "SCHEDULE"
    )
    app = create_app(settings)  # background services on, as in production
    app.state.mail.queue = StubMailQueue()
    with TestClient(app):
        db.expire_all()
        row = db.get(MailJob, job.id)
        assert row.state == JobState.INTERRUPTED  # closed, not marked failed
        assert row.message == INTERRUPTED_MAIL_MSG
        assert app.state.mail.queue.requests == [(acc.id, "backup", {}, "RECOVERY")]
        assert app.state.scheduler.aps.get_job("mail") is not None


async def test_disconnecting_keeps_the_mail_and_its_search_rows(mail_app, db):
    from sqlalchemy import text

    app, acc, _ = mail_app
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    await app.state.mail.handle(acc.id, "backup", {}, "MANUAL")
    await app.state.accounts.disconnect(acc.id)
    # The account row stays (nothing removes accounts), so its mail stays searchable.
    assert db.execute(text("SELECT count(*) FROM mail_fts")).scalar() == 3


def test_with_the_disk_missing_a_mail_job_left_running_is_closed_but_not_queued(
    settings, make_account, db
):
    from fastapi.testclient import TestClient

    from app.main import create_app

    first = create_app(settings, start_background=False)  # writes the volume file
    acc = make_account()
    ensure_services(db, acc)
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    job = start_mail_job(
        db, first.state.layout, first.state.bus, acc.id, MailJobKind.CHECK, "SCHEDULE"
    )
    first.state.layout.volume_file.unlink()
    app = create_app(settings)
    app.state.mail.queue = StubMailQueue()
    with TestClient(app):
        assert app.state.queue.disabled_reason == MISSING_DISK_MSG
        db.expire_all()
        assert db.get(MailJob, job.id).state == JobState.INTERRUPTED
        assert app.state.mail.queue.requests == []


async def test_a_backup_that_crashes_waits_for_the_interval_before_running_again(mail_app, db):
    app, acc, _ = mail_app
    mail = app.state.mail
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)

    async def boom(_run):
        raise RuntimeError("the mail folder is read-only")

    mail.backup.run = boom
    await mail.handle(acc.id, "backup", {}, "MANUAL")
    db.expire_all()
    job = db.query(MailJob).one()
    assert job.state == JobState.FAILED and "read-only" in job.message
    from app.services.mail.index import sync_state

    st = sync_state(db, acc.id)
    assert st.last_run_at is not None and "read-only" in st.last_error
    await mail.tick()
    assert mail.queue.requests == []  # not again every minute


async def test_a_crash_while_picking_the_adapter_is_recorded_too(mail_app, db):
    app, acc, _ = mail_app
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)

    class Broken:
        def for_account(self, account):
            raise LookupError("No mail for this account")

    app.state.mail.adapters = Broken()
    await app.state.mail.handle(acc.id, "backup", {}, "MANUAL")
    db.expire_all()
    assert db.query(MailJob).one().state == JobState.FAILED
    await app.state.mail.tick()
    assert app.state.mail.queue.requests == []


async def test_the_tick_queues_nothing_while_the_disk_is_missing(mail_app, db):
    app, acc, _ = mail_app
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    app.state.queue.disabled_reason = MISSING_DISK_MSG
    await app.state.mail.tick()
    assert app.state.mail.queue.requests == []
