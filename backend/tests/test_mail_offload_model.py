from datetime import UTC, datetime

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
