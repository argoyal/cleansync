from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.devtools.fake_gmail import make_mail
from app.models import Event, MailMessage
from app.models.enums import MailState
from app.services.events.bus import EventBus
from app.services.mail.archive import ArchiveProblem, delete_forever
from app.services.mail.offload import restore_targets
from app.services.mail.offload_plan import Pick
from app.services.mail.store import eml_path
from tests.rules_helpers import keep
from tests.test_mail_restore import _offloaded, _restore

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def archived(db, layout, acc, subject):
    row = keep(db, layout, acc, make_mail(subject))
    row.state = MailState.LOCAL_ARCHIVE
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
    assert restore_targets(db, acc.id, Pick(q="promo", archive=True), None, NOW) == [a.id]
    assert restore_targets(db, acc.id, Pick(message_ids=[a.id]), None, NOW) == [a.id]


def _archive(box, *subjects):
    with box.app.state.session_factory() as db:
        for s in subjects:
            row = db.scalar(select(MailMessage).where(MailMessage.subject == s))
            row.state, row.offload_id = MailState.LOCAL_ARCHIVE, None
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
    r = _restore(auth_client, box, q="statement", archive=True)
    assert r.status_code == 202 and r.json()["count"] == 1
    await box.run_queued()
    assert box.row("Old statement").state == MailState.IN_MAIL
