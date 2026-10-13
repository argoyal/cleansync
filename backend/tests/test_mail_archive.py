from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.devtools.fake_gmail import make_mail
from app.models import Event, MailJob, MailMessage, MailOffloadItem, RuleDecision, RuleTodo
from app.models.enums import JobState, MailJobKind, MailState
from app.services.events.bus import EventBus
from app.services.mail.archive import ArchiveProblem, delete_forever
from app.services.mail.offload import restore_targets
from app.services.mail.offload_plan import Pick
from app.services.mail.store import eml_path
from tests.rules_helpers import keep
from tests.test_mail_restore import _copies, _lose_first_import_answer, _offloaded, _restore

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def archived(db, layout, acc, subject):
    row = keep(db, layout, acc, make_mail(subject))
    row.state = MailState.OFFLOADED
    db.commit()
    return row


def test_delete_forever_removes_file_row_and_search_entry(db, layout, make_account):
    acc = make_account()
    a = archived(db, layout, acc, "Old promo")
    kept = keep(db, layout, acc, make_mail("Still here"))
    path = eml_path(layout, acc.email, a.file_path)
    a_id = a.id
    res = delete_forever(
        db,
        layout,
        EventBus(),
        acc.id,
        acc.email,
        message_ids=[a.id, kept.id],
        q="",
        confirm=1,
        now=NOW,
    )
    assert (res.deleted, res.left) == (1, 1)
    assert not path.exists() and db.get(MailMessage, a_id) is None
    assert (
        db.execute(text("SELECT count(*) FROM mail_fts WHERE rowid = :i"), {"i": a_id}).scalar()
        == 0
    )
    ev = db.scalar(select(Event).where(Event.kind == "mail_deleted_forever"))
    assert "Old promo" in str(ev.data)
    assert db.get(MailMessage, kept.id) is not None
    assert eml_path(layout, acc.email, kept.file_path).exists()


def test_typed_count_must_match(db, layout, make_account):
    acc = make_account()
    a = archived(db, layout, acc, "x")
    with pytest.raises(ArchiveProblem, match="doesn't match"):
        delete_forever(
            db, layout, EventBus(), acc.id, acc.email, message_ids=[a.id], q="", confirm=2, now=NOW
        )
    assert db.get(MailMessage, a.id) is not None


def test_nothing_in_the_archive_is_refused(db, layout, make_account):
    acc = make_account()
    kept = keep(db, layout, acc, make_mail("In Gmail"))
    with pytest.raises(ArchiveProblem, match="local archive"):
        delete_forever(
            db,
            layout,
            EventBus(),
            acc.id,
            acc.email,
            message_ids=[kept.id],
            q="",
            confirm=0,
            now=NOW,
        )
    assert db.get(MailMessage, kept.id) is not None


def test_by_archive_search(db, layout, make_account):
    acc = make_account()
    archived(db, layout, acc, "promo one")
    archived(db, layout, acc, "receipt")
    res = delete_forever(
        db, layout, EventBus(), acc.id, acc.email, message_ids=None, q="promo", confirm=1, now=NOW
    )
    assert res.deleted == 1


def test_restore_targets_include_the_archive(db, layout, make_account):
    acc = make_account()
    a = archived(db, layout, acc, "promo")
    assert restore_targets(db, acc.id, Pick(q="promo"), None, NOW) == [a.id]
    assert restore_targets(db, acc.id, Pick(message_ids=[a.id]), None, NOW) == [a.id]


def _archive(box, *subjects):
    with box.app.state.session_factory() as db:
        for s in subjects:
            row = db.scalar(select(MailMessage).where(MailMessage.subject == s))
            row.state, row.offload_id = MailState.OFFLOADED, None
        db.commit()


async def test_archived_mail_restores_untrashed_or_imported(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    in_trash, gone = box.row("Old statement"), box.row("Old photos")
    _archive(box, "Old statement", "Old photos")
    box.fake.remove(gone.provider_id)  # Gmail emptied its trash
    r = _restore(auth_client, box, message_ids=[in_trash.id, gone.id])
    assert r.status_code == 202 and r.json()["count"] == 2
    await box.run_queued()
    assert box.row("Old statement").state == MailState.IN_MAIL
    assert box.row("Old statement").provider_id == in_trash.provider_id
    assert "TRASH" not in box.fake.messages[in_trash.provider_id].labels
    back = box.row("Old photos")
    assert back.state == MailState.IN_MAIL and back.provider_id != gone.provider_id
    assert box.fake.messages[back.provider_id].raw


async def test_restore_the_whole_archive_by_search(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)
    _archive(box, "Old statement")
    r = _restore(auth_client, box, q="statement")
    assert r.status_code == 202 and r.json()["count"] == 1
    await box.run_queued()
    assert box.row("Old statement").state == MailState.IN_MAIL


async def test_a_lost_import_answer_for_an_archived_mail_never_makes_a_second_copy(
    auth_client, offload_box
):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    _archive(box, "Old photos")
    box.fake.remove(row.provider_id)
    _lose_first_import_answer(box)
    _restore(auth_client, box, message_ids=[row.id])
    await box.run_queued()
    await box.backup()  # the check sees the imported mail
    (copy,) = _copies(box, "Old photos")
    (after,) = [r for r in box.rows_all() if r.subject == "Old photos"]
    assert after.id == row.id and after.provider_id == copy.id
    assert after.state == MailState.IN_MAIL and after.import_started_at is None


def _restore_job(db, acc, state):
    db.add(MailJob(account_id=acc.id, kind=MailJobKind.RESTORE, trigger="MANUAL", state=state))
    db.commit()


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING, JobState.PAUSED])
def test_delete_forever_waits_for_a_restore(db, layout, make_account, state):
    acc = make_account()
    a = archived(db, layout, acc, "x")
    _restore_job(db, acc, state)
    with pytest.raises(ArchiveProblem, match="restore is running"):
        delete_forever(
            db, layout, EventBus(), acc.id, acc.email, message_ids=[a.id], q="", confirm=1, now=NOW
        )
    assert db.get(MailMessage, a.id) is not None


def test_delete_forever_waits_for_a_restore_not_started_yet(db, layout, make_account):
    acc = make_account()
    a = archived(db, layout, acc, "x")
    with pytest.raises(ArchiveProblem, match="restore is running"):
        delete_forever(
            db,
            layout,
            EventBus(),
            acc.id,
            acc.email,
            message_ids=[a.id],
            q="",
            confirm=1,
            now=NOW,
            restore_queued=lambda account_id: account_id == acc.id,
        )
    assert db.get(MailMessage, a.id) is not None


def test_a_finished_restore_does_not_block(db, layout, make_account):
    acc = make_account()
    a = archived(db, layout, acc, "x")
    _restore_job(db, acc, JobState.SUCCESS)
    res = delete_forever(
        db, layout, EventBus(), acc.id, acc.email, message_ids=[a.id], q="", confirm=1, now=NOW
    )
    assert res.deleted == 1


@pytest.mark.parametrize("ids", [None, []])
def test_nothing_picked_and_no_search_is_refused(db, layout, make_account, ids):
    acc = make_account()
    a = archived(db, layout, acc, "x")
    with pytest.raises(ArchiveProblem, match="Pick mails or type a search"):
        delete_forever(
            db, layout, EventBus(), acc.id, acc.email, message_ids=ids, q=" ", confirm=1, now=NOW
        )
    assert db.get(MailMessage, a.id) is not None


def test_the_wrong_count_message_says_one_mail(db, layout, make_account):
    acc = make_account()
    a = archived(db, layout, acc, "x")
    with pytest.raises(ArchiveProblem, match=r"doesn't match the 1 mail picked\."):
        delete_forever(
            db, layout, EventBus(), acc.id, acc.email, message_ids=[a.id], q="", confirm=5, now=NOW
        )


def test_a_file_that_cannot_be_removed_does_not_fail_the_delete(
    db, layout, make_account, monkeypatch, caplog
):
    acc = make_account()
    a = archived(db, layout, acc, "x")
    a_id = a.id

    def boom(self, missing_ok=False):
        raise PermissionError("denied")

    monkeypatch.setattr("pathlib.Path.unlink", boom)
    res = delete_forever(
        db, layout, EventBus(), acc.id, acc.email, message_ids=[a_id], q="", confirm=1, now=NOW
    )
    assert res.deleted == 1 and db.get(MailMessage, a_id) is None
    assert "denied" in caplog.text


async def test_a_mail_deleted_during_a_restore_is_counted_not_restored(
    auth_client, offload_box, monkeypatch
):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    _archive(box, "Old photos")
    adapter = box.mail.adapter_for(box.acc)
    real = adapter.state

    async def state_then_gone(pid):
        with box.app.state.session_factory() as db:
            if (gone := db.get(MailMessage, row.id)) is not None:
                db.delete(gone)
                db.commit()
        return await real(pid)

    monkeypatch.setattr(adapter, "state", state_then_gone)
    out = await box.offloads._restore_one(box.acc, adapter, row.id, None)
    assert out == "failed"


async def test_deleting_forever_takes_its_rule_rows_and_offload_item_with_it(
    auth_client, offload_box, db
):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    off_id = row.offload_id
    with box.app.state.session_factory() as s:
        s.get(MailMessage, row.id).state = MailState.OFFLOADED
        s.add(RuleDecision(account_id=box.acc.id, message_id=row.id))
        s.add(RuleTodo(account_id=box.acc.id, message_id=row.id))
        s.commit()
    res = delete_forever(
        db,
        box.mail.layout,
        EventBus(),
        box.acc.id,
        box.acc.email,
        message_ids=[row.id],
        q="",
        confirm=1,
        now=NOW,
    )
    assert res.deleted == 1
    db.expire_all()
    assert db.scalar(select(RuleDecision).where(RuleDecision.message_id == row.id)) is None
    assert db.scalar(select(RuleTodo).where(RuleTodo.message_id == row.id)) is None
    assert db.scalar(select(MailOffloadItem).where(MailOffloadItem.message_id == row.id)) is None
    assert auth_client.get(f"/api/mail/offloads/{off_id}").status_code == 200
