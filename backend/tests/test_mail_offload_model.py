from datetime import UTC, datetime

import pytest

from app.models import MailMessage, MailOffload, MailOffloadItem
from app.models.enums import MailItemResult, MailOffloadState
from app.services.mail.offload_plan import INTERRUPTED_OFFLOAD_MSG, recover_mail_offloads


def _message(db, acc):
    m = MailMessage(
        account_id=acc.id,
        provider_id="m1",
        thread_id="m1",
        date=datetime(2024, 1, 1, tzinfo=UTC),
        size=10,
        file_size=10,
        sha256="0" * 64,
        labels=["INBOX"],
        file_path="2024/01/m1.eml",
    )
    db.add(m)
    db.commit()
    return m


def test_an_offload_and_its_items_have_sensible_defaults(db, make_account):
    acc = make_account()
    m = _message(db, acc)
    off = MailOffload(account_id=acc.id, filter_text="older_than:1y", total=1)
    db.add(off)
    db.flush()
    db.add(MailOffloadItem(offload_id=off.id, message_id=m.id))
    db.commit()
    assert off.state == MailOffloadState.WAITING and off.done == 0 and off.skipped == 0
    item = db.query(MailOffloadItem).one()
    assert item.result == MailItemResult.PENDING


def test_a_stop_fails_a_running_offload_but_a_waiting_one_keeps_waiting(db, make_account):
    acc = make_account()
    rows = {
        s: MailOffload(account_id=acc.id, filter_text="x", total=3, state=s)
        for s in (MailOffloadState.WAITING, MailOffloadState.RUNNING, MailOffloadState.DONE)
    }
    db.add_all(rows.values())
    db.commit()
    assert recover_mail_offloads(db) == 1
    db.expire_all()
    running = rows[MailOffloadState.RUNNING]
    assert running.state == MailOffloadState.FAILED and running.reason == INTERRUPTED_OFFLOAD_MSG
    assert running.finished_at is not None
    for s in (MailOffloadState.WAITING, MailOffloadState.DONE):
        assert rows[s].state == s and rows[s].reason is None and rows[s].finished_at is None


@pytest.mark.parametrize("disk_missing", [False, True])
def test_at_startup_a_cut_short_offload_and_its_job_both_end_failed(
    settings, make_account, db, disk_missing
):
    """Activity and the offload's page agree: the job is FAILED with the offload's reason,
    not 'interrupted, goes on at the next check'."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.models import MailJob
    from app.models.enums import JobState, MailJobKind
    from app.services.mail.jobs import start_mail_job
    from tests.test_mail_api import StubMailQueue

    first = create_app(settings, start_background=False)  # writes the volume file
    acc = make_account()
    off = MailOffload(account_id=acc.id, filter_text="x", total=3, state=MailOffloadState.RUNNING)
    db.add(off)
    db.commit()
    job = start_mail_job(
        db, first.state.layout, first.state.bus, acc.id, MailJobKind.OFFLOAD, "MANUAL", 3
    )
    other = make_account("bob@example.com")
    backup = start_mail_job(
        db, first.state.layout, first.state.bus, other.id, MailJobKind.BACKUP, "SCHEDULE", None
    )
    if disk_missing:
        first.state.layout.volume_file.unlink()
    app = create_app(settings)  # background services on, as in production
    app.state.mail.queue = StubMailQueue()
    with TestClient(app):
        db.expire_all()
        assert db.get(MailOffload, off.id).state == MailOffloadState.FAILED
        row = db.get(MailJob, job.id)
        assert row.state == JobState.FAILED and row.message == INTERRUPTED_OFFLOAD_MSG
        assert row.completed_at is not None
        assert db.get(MailJob, backup.id).state == JobState.INTERRUPTED  # other jobs as before


def test_with_the_disk_missing_a_failing_offload_recovery_still_closes_mail_jobs(
    settings, make_account, db, monkeypatch
):
    """Each startup step on its own: one failing must not skip the next."""
    from fastapi.testclient import TestClient

    import app.main as main_mod
    from app.main import create_app
    from app.models import MailJob
    from app.models.enums import JobState, MailJobKind
    from app.services.mail.jobs import start_mail_job
    from tests.test_mail_api import StubMailQueue

    first = create_app(settings, start_background=False)
    acc = make_account()
    job = start_mail_job(
        db, first.state.layout, first.state.bus, acc.id, MailJobKind.CHECK, "SCHEDULE", None
    )
    first.state.layout.volume_file.unlink()

    def broken(db):
        raise RuntimeError("boom")

    monkeypatch.setattr(main_mod, "recover_mail_offloads", broken)
    app = create_app(settings)
    app.state.mail.queue = StubMailQueue()
    with TestClient(app):
        db.expire_all()
        assert db.get(MailJob, job.id).state == JobState.INTERRUPTED
