import pytest

from app.models import MailMessage
from app.models.enums import JobState, MailPhase, MailState
from app.services.mail.store import eml_path
from tests.mail_helpers import every_row_has_its_file


def _row(box, mid) -> MailMessage:
    return next(m for m in box.rows() if m.provider_id == mid)


async def _backed_up(box, n=3):
    ids = box.add(n)
    state, _, _ = await box.run()
    assert state == JobState.SUCCESS and box.state().phase == MailPhase.INCREMENTAL
    return ids


async def test_new_mail_and_label_changes_are_picked_up(box):
    ids = await _backed_up(box)
    new = box.fake.add(b"Subject: New\r\nFrom: a@example.com\r\n\r\nhi", labels=("INBOX", "UNREAD"))
    box.fake.set_labels(ids[0], ("INBOX", "STARRED"))
    draft = box.fake.add(b"Subject: Draft\r\n\r\nx", labels=("DRAFT",))
    state, message, r = await box.run()
    assert (
        state == JobState.SUCCESS and r.downloaded == 1 and message.startswith("Saved 1 new mail")
    )
    assert _row(box, new).unread is True
    assert _row(box, ids[0]).starred is True and _row(box, ids[0]).labels == ["INBOX", "STARRED"]
    assert draft not in {m.provider_id for m in box.rows()}
    assert box.state().history_id == str(box.fake.history_id)


async def test_trash_and_back_in_gmail(box):
    ids = await _backed_up(box)
    box.fake.set_labels(ids[0], ("TRASH",))  # the owner deletes it in Gmail
    box.fake.set_labels(ids[1], ("SPAM",))
    await box.run()
    assert _row(box, ids[0]).state == MailState.DELETED
    assert _row(box, ids[0]).labels == ["INBOX"]  # the labels it had are kept for a restore
    assert _row(box, ids[1]).state == MailState.DELETED
    box.fake.set_labels(ids[0], ("INBOX",))  # and takes it back out of the trash
    await box.run()
    assert _row(box, ids[0]).state == MailState.IN_MAIL
    box.fake.remove(ids[2])  # deleted for good in Gmail
    await box.run()
    assert _row(box, ids[2]).state == MailState.DELETED
    every_row_has_its_file(box)  # mail deleted in Gmail is never deleted here


async def test_offloaded_mail_leaving_the_trash_stays_offloaded(box):
    ids = await _backed_up(box)
    with box.sf() as db:
        for mid in ids[:2]:
            row = db.query(MailMessage).filter_by(provider_id=mid).one()
            row.state, row.offload_id = MailState.OFFLOADED, 7
        db.commit()
    box.fake.set_labels(ids[0], ("TRASH",))  # CleanSync's own offload, seen in Gmail's history
    box.fake.remove(ids[0])  # Gmail empties its trash 30 days later
    box.fake.set_labels(ids[1], ("TRASH",))
    await box.run()
    assert _row(box, ids[0]).state == MailState.OFFLOADED
    assert _row(box, ids[0]).labels == ["INBOX"]  # no change (spec §4.4)
    box.fake.set_labels(ids[1], ("INBOX",))  # the owner took an offloaded mail back in Gmail
    await box.run()
    assert _row(box, ids[1]).state == MailState.IN_MAIL and _row(box, ids[1]).offload_id is None


async def test_offloaded_mail_moved_from_the_trash_to_spam_is_deleted_in_gmail(box):
    ids = await _backed_up(box, n=1)
    with box.sf() as db:
        row = db.query(MailMessage).filter_by(provider_id=ids[0]).one()
        row.state, row.offload_id = MailState.OFFLOADED, 7
        db.commit()
    box.fake.set_labels(ids[0], ("TRASH",))
    await box.run()
    box.fake.set_labels(ids[0], ("SPAM",))
    await box.run()
    # Still on its offload's page (controller ruling).
    assert _row(box, ids[0]).state == MailState.DELETED and _row(box, ids[0]).offload_id == 7
    assert _row(box, ids[0]).labels == ["INBOX"]


async def test_history_too_old_runs_a_recheck_that_never_deletes_files(box):
    ids = await _backed_up(box, n=4)
    box.fake.forget_history()
    gone = ids[0]
    del box.fake.messages[gone]  # gone from Gmail with no history to say so
    missing = box.fake.add(b"Subject: Missed\r\nFrom: a@example.com\r\n\r\nhi", labels=("INBOX",))
    box.fake.forget_history()
    state, _, _ = await box.run()
    assert state == JobState.SUCCESS
    assert _row(box, gone).state == MailState.DELETED
    assert eml_path(box.layout, box.acc.email, _row(box, gone).file_path).is_file()
    assert _row(box, missing).state == MailState.IN_MAIL
    st = box.state()
    assert st.phase == MailPhase.INCREMENTAL and st.history_id == str(box.fake.history_id)


async def test_a_recheck_cut_short_marks_nothing_deleted_and_resumes(box):
    ids = await _backed_up(box, n=6)
    ids += box.add(1)  # Gmail moves on…
    box.fake.forget_history()  # …and forgets the history CleanSync would start from
    # Disk checks: 1 before keeping up, 2 before re-check page 1; the 3rd finds the disk gone.
    state, _, _ = await box.run(stop_after_pages=2)
    assert state == JobState.PAUSED
    assert box.state().phase == MailPhase.RECHECK
    assert all(m.state == MailState.IN_MAIL for m in box.rows())  # nothing marked half way
    state, _, _ = await box.run()
    assert state == JobState.SUCCESS
    assert all(m.state == MailState.IN_MAIL for m in box.rows())
    assert box.state().phase == MailPhase.INCREMENTAL
    with box.sf() as db:
        from app.models import MailRecheckSeen

        assert db.query(MailRecheckSeen).count() == 0
    assert sorted(m.provider_id for m in box.rows()) == sorted(ids)


@pytest.mark.parametrize("labels", [("SPAM",), ("TRASH",)])
async def test_new_mail_arriving_straight_in_spam_or_trash_is_not_kept(box, labels):
    await _backed_up(box)
    mid = box.fake.add(b"Subject: x\r\n\r\ny", labels=labels)
    await box.run()
    assert mid not in {m.provider_id for m in box.rows()}


async def test_a_recheck_returns_mail_restored_in_gmail_to_in_mail(box):
    ids = await _backed_up(box)
    box.fake.set_labels(ids[0], ("TRASH",))  # the owner deletes it in Gmail
    await box.run()
    assert _row(box, ids[0]).state == MailState.DELETED
    box.fake.set_labels(ids[0], ("INBOX",))  # takes it back out of the trash…
    box.fake.forget_history()  # …and Gmail no longer has the history that says so
    state, _, _ = await box.run()
    assert state == JobState.SUCCESS and box.state().phase == MailPhase.INCREMENTAL
    assert _row(box, ids[0]).state == MailState.IN_MAIL
    assert _row(box, ids[0]).labels == ["INBOX"]


async def test_a_recheck_returns_offloaded_mail_taken_back_out_of_the_trash(box):
    ids = await _backed_up(box)
    with box.sf() as db:
        row = db.query(MailMessage).filter_by(provider_id=ids[0]).one()
        row.state, row.offload_id = MailState.OFFLOADED, 7
        db.commit()
    box.fake.set_labels(ids[0], ("TRASH",))  # CleanSync's own offload
    box.fake.set_labels(ids[1], ("TRASH",))  # another offloaded mail that stays in the trash
    with box.sf() as db:
        row = db.query(MailMessage).filter_by(provider_id=ids[1]).one()
        row.state, row.offload_id = MailState.OFFLOADED, 7
        db.commit()
    await box.run()
    box.fake.set_labels(ids[0], ("INBOX",))  # the owner takes it back in Gmail
    box.fake.forget_history()
    state, _, _ = await box.run()
    assert state == JobState.SUCCESS
    assert _row(box, ids[0]).state == MailState.IN_MAIL and _row(box, ids[0]).offload_id is None
    assert _row(box, ids[1]).state == MailState.OFFLOADED and _row(box, ids[1]).offload_id == 7


async def test_mail_taken_out_of_spam_is_backed_up(box):
    await _backed_up(box)
    mid = box.fake.add(b"Subject: Not spam\r\nFrom: a@example.com\r\n\r\nhi", labels=("SPAM",))
    await box.run()
    assert mid not in {m.provider_id for m in box.rows()}
    box.fake.set_labels(mid, ("INBOX",))  # the owner presses "Not spam": a label change
    state, _, r = await box.run()
    assert state == JobState.SUCCESS and r.downloaded == 1
    assert _row(box, mid).state == MailState.IN_MAIL and _row(box, mid).labels == ["INBOX"]
    every_row_has_its_file(box)
