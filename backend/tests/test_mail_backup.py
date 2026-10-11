from datetime import timedelta

import pytest

from app.models import Account
from app.models.enums import JobState, MailPhase, ServiceKind, ServiceState
from app.services.accounts.oauth import DRIVE_SCOPE, GMAIL_SCOPE
from app.services.accounts.services import service_state
from app.services.google.quota import BUDGET_MSG, ApiBudget
from app.services.mail import backup as backup_mod
from app.services.mail import index
from app.services.mail.backup import DISK_LOW_MSG, TROUBLE_PAUSE_MSG
from app.services.mail.gmail import API_OFF_MSG
from app.services.queue import MISSING_DISK_MSG
from tests.mail_helpers import GB, every_row_has_its_file


async def test_the_first_backup_keeps_every_mail_but_spam_trash_drafts_and_chats(box):
    kept = box.add(5)
    box.add(1, labels=("SPAM",))
    box.add(1, labels=("DRAFT",))
    box.add(1, labels=("CHAT",))
    start_history = str(box.fake.history_id)
    state, message, r = await box.run()
    assert state == JobState.SUCCESS
    assert sorted(m.provider_id for m in box.rows()) == sorted(kept)
    assert r.downloaded == 5 and message.startswith("Saved 5 new mails")
    every_row_has_its_file(box)
    st = box.state()
    assert st.phase == MailPhase.INCREMENTAL and st.cursor is None
    assert (
        st.history_id == start_history
    )  # taken at the start: mail that arrived meanwhile is caught
    assert st.done == 5 and st.total == 8 and st.last_error is None


async def test_a_missing_disk_pauses_the_backup_and_it_resumes_later(box):
    box.add(6)
    state, message, r = await box.run(stop_after_pages=1)  # the disk drops out after page 1
    assert state == JobState.PAUSED and message == MISSING_DISK_MSG
    assert len(box.rows()) == 2 and box.state().cursor == "2"
    assert box.state().last_error == MISSING_DISK_MSG
    before = len(box.fetches())
    state, _, _ = await box.run()  # the disk is back
    assert state == JobState.SUCCESS
    assert len(box.rows()) == 6
    assert len(box.fetches()) - before == 4  # page 1's mail was not downloaded again
    assert any(c[2].get("pageToken") == "2" for c in box.fake.calls)
    every_row_has_its_file(box)


async def test_a_crash_between_file_and_row_heals_on_the_next_run(box, monkeypatch):
    ids = box.add(2)
    real = backup_mod.add_message
    calls = {"n": 0}

    def crash_once(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("power cut")
        return real(*a, **kw)

    monkeypatch.setattr(backup_mod, "add_message", crash_once)
    with pytest.raises(RuntimeError):
        await box.run()
    assert box.rows() == []  # never a row without its file, but a file without a row is fine
    monkeypatch.setattr(backup_mod, "add_message", real)
    state, _, _ = await box.run()
    assert state == JobState.SUCCESS
    assert sorted(m.provider_id for m in box.rows()) == sorted(ids)
    every_row_has_its_file(box)


async def test_one_failing_mail_never_stops_the_run_and_is_listed_after_three_tries(box):
    ids = box.add(4)
    bad = ids[1]
    box.fake.fail("GET", rf"/messages/{bad}$", 500, times=99)
    state, message, r = await box.run()
    assert state == JobState.SUCCESS and r.failed == 1
    assert "1 couldn't be saved" in message
    assert len(box.rows()) == 3
    for _ in range(2):
        box.clock.now += timedelta(days=1)  # past the back-off
        await box.run()
    with box.sf() as db:
        shown = index.shown_failures(db, box.acc.id)
    assert [f.provider_id for f in shown] == [bad] and shown[0].attempts == 3
    box.fake._failures.clear()  # Gmail serves it again
    box.clock.now += timedelta(days=1)
    await box.run()
    assert len(box.rows()) == 4
    with box.sf() as db:
        assert index.shown_failures(db, box.acc.id) == []


async def test_the_disk_floor_pauses_before_anything_is_written(box):
    box.add(3)
    box.free = 1 * GB
    state, message, _ = await box.run()
    assert state == JobState.PAUSED
    assert message == DISK_LOW_MSG.format(floor=2)
    assert box.rows() == []
    assert not any(box.layout.mail_root.rglob("*.eml"))


async def test_the_budget_pauses_until_the_next_utc_day(box, session_factory):
    box.add(5)
    # labels 1 + profile 1 + one list 5 + two mails 20 each = 47
    box.budget = ApiBudget(session_factory, budget=lambda: 47, clock=box.clock)
    state, message, _ = await box.run(page_size=5)
    assert state == JobState.PAUSED and message == BUDGET_MSG.format(budget=47)
    assert len(box.rows()) == 2
    box.clock.now += timedelta(days=1)
    state, _, _ = await box.run(page_size=5)
    assert state == JobState.PAUSED and len(box.rows()) == 4  # goes on the next UTC day


async def test_a_revoked_mail_permission_asks_to_reconnect_mail(box, db):
    box.add(1)
    with box.sf() as s:
        s.get(Account, box.acc.id).scopes = f"{DRIVE_SCOPE} {GMAIL_SCOPE}"
        s.commit()
    box.fake.fail("GET", r"/labels$", 403, reason="insufficientPermissions")
    state, message, r = await box.run()
    assert state == JobState.FAILED and "Set up mail again" in message
    assert r.login_lost is False
    db.expire_all()
    assert service_state(db, box.acc.id, ServiceKind.MAIL) == ServiceState.NEEDS_RECONNECT
    # The saved permission no longer says Gmail, so "Reconnect mail" asks Google again.
    assert db.get(Account, box.acc.id).scopes == DRIVE_SCOPE
    box.fake.revoke()
    _, _, r = await box.run()
    assert r.login_lost is True  # the whole login is gone: the caller marks the account


async def test_a_missing_disk_stops_failed_mails_being_tried_again(box):
    ids = box.add(3)
    box.fake.fail("GET", rf"/messages/{ids[0]}$", 500, times=1)
    await box.run()
    assert len(box.rows()) == 2
    box.clock.now += timedelta(days=1)  # the failed mail is due again
    state, message, _ = await box.run(stop_after_pages=0)  # but the disk is gone
    assert state == JobState.PAUSED and message == MISSING_DISK_MSG
    assert len(box.rows()) == 2
    assert not any(box.layout.mail_root.rglob(f"{ids[0]}.eml"))


async def test_the_gmail_api_turned_off_says_how_to_turn_it_on(box, db):
    box.add(1)
    box.fake.fail("GET", r"/labels$", 403, reason="accessNotConfigured")
    state, message, r = await box.run()
    assert state == JobState.FAILED and message == API_OFF_MSG
    assert r.login_lost is False
    db.expire_all()
    # Not the set-up-again path: the permission is fine, the project is not.
    assert service_state(db, box.acc.id, ServiceKind.MAIL) == ServiceState.ACTIVE


async def test_gmail_trouble_mid_page_pauses_after_three_in_a_row_and_resumes(box):
    box.add(6)
    box.fake.fail("GET", r"/messages/[^/]+$", 503, times=99)
    state, message, r = await box.run(page_size=6)
    assert state == JobState.PAUSED
    assert message == TROUBLE_PAUSE_MSG.format(error="Gmail's servers had a problem (HTTP 503)")
    assert len(box.fetches()) == 3  # stopped after three in a row, not one try per mail
    assert r.failed == 0
    with box.sf() as db:
        assert index.due_failures(db, box.acc.id, box.clock.now + timedelta(days=9)) == []
    st = box.state()
    assert st.phase == MailPhase.INITIAL and st.cursor is None and st.last_error == message
    box.fake._failures.clear()  # Gmail is back
    state, _, _ = await box.run(page_size=6)
    assert state == JobState.SUCCESS and len(box.rows()) == 6


async def test_one_or_two_troubled_mails_between_good_ones_go_on_the_failed_list(box):
    ids = box.add(5)
    box.fake.fail("GET", rf"/messages/({ids[1]}|{ids[3]})$", 503, times=99)
    state, message, r = await box.run(page_size=5)
    assert state == JobState.SUCCESS and r.failed == 2
    assert len(box.rows()) == 3


async def test_mails_gmail_refuses_for_themselves_still_go_on_the_failed_list(box):
    box.add(4)
    box.fake.fail("GET", r"/messages/[^/]+$", 400, times=99)
    state, message, r = await box.run(page_size=4)
    assert state == JobState.SUCCESS and r.failed == 4  # never paused for these
    assert "4 couldn't be saved" in message


async def test_reading_a_mail_runs_off_the_event_loop(box, monkeypatch):
    import threading

    box.add(1)
    loop_thread = threading.current_thread()
    seen = []
    real = backup_mod.parse_eml

    def watch(raw):
        seen.append(threading.current_thread())
        return real(raw)

    monkeypatch.setattr(backup_mod, "parse_eml", watch)
    await box.run()
    assert seen and all(t is not loop_thread for t in seen)  # a big mail can't freeze the app
