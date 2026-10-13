"""Delete forever in Offloaded and Deleted in Gmail (mail panel spec §7)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.devtools.fake_gmail import make_mail
from app.models import Event, MailJob, MailMessage, MailOffloadItem, RuleDecision, RuleTodo
from app.models.enums import DecisionHow, DecisionStatus, JobState, MailJobKind, MailState
from app.services.events.bus import EventBus
from app.services.mail.forever import ForeverProblem, delete_forever
from app.services.mail.store import eml_path
from tests.rules_helpers import keep
from tests.test_mail_restore import _offloaded

NOW = datetime(2026, 10, 13, tzinfo=UTC)
NAMES = {"offloaded": "Offloaded", "deleted": "Deleted in Gmail"}
BOTH = [("offloaded", MailState.OFFLOADED), ("deleted", MailState.DELETED)]


def kept(db, layout, acc, subject, state):
    row = keep(db, layout, acc, make_mail(subject))
    row.state = state
    db.commit()
    return row


def forever(db, layout, acc, **kw):
    kw.setdefault("now", NOW)
    return delete_forever(db, layout, EventBus(), acc.id, acc.email, **kw)


@pytest.mark.parametrize("view,state", BOTH)
def test_picked_mails_lose_file_row_search_entry_and_rule_history(
    db, layout, make_account, view, state
):
    acc = make_account()
    a = kept(db, layout, acc, "Old promo", state)
    in_gmail = keep(db, layout, acc, make_mail("Still in Gmail"))
    db.add(
        RuleDecision(
            account_id=acc.id, message_id=a.id, how=DecisionHow.AI, status=DecisionStatus.APPLIED
        )
    )
    db.commit()
    path, a_id = eml_path(layout, acc.email, a.file_path), a.id
    res = forever(db, layout, acc, view=view, message_ids=[a.id, in_gmail.id], confirm=1)
    assert (res.deleted, res.left) == (1, 1)
    assert not path.exists() and db.get(MailMessage, a_id) is None
    rows = db.execute(text("SELECT count(*) FROM mail_fts WHERE rowid = :i"), {"i": a_id})
    assert rows.scalar() == 0
    assert db.scalar(select(RuleDecision).where(RuleDecision.message_id == a_id)) is None
    assert db.get(MailMessage, in_gmail.id) is not None  # mail in Gmail is never touched
    assert eml_path(layout, acc.email, in_gmail.file_path).exists()
    ev = db.scalar(select(Event).where(Event.kind == "mail_deleted_forever"))
    assert ev.message == f"Deleted 1 mail forever from {NAMES[view]}"
    assert "Old promo" in str(ev.data)


@pytest.mark.parametrize("view,state", BOTH)
def test_a_pick_all_is_the_view_and_the_search(db, layout, make_account, view, state):
    acc = make_account()
    kept(db, layout, acc, "promo one", state)
    kept(db, layout, acc, "promo two", state)
    kept(db, layout, acc, "receipt", state)
    other = MailState.DELETED if state == MailState.OFFLOADED else MailState.OFFLOADED
    kept(db, layout, acc, "promo elsewhere", other)
    keep(db, layout, acc, make_mail("promo in Gmail"))
    assert forever(db, layout, acc, view=view, q="promo", confirm=2).deleted == 2
    assert forever(db, layout, acc, view=view, q="", confirm=1).deleted == 1  # the whole view
    left = sorted(db.scalars(select(MailMessage.subject)))
    assert left == ["promo elsewhere", "promo in Gmail"]


def test_a_count_typed_for_an_older_pick_all_is_refused(db, layout, make_account):
    acc = make_account()
    kept(db, layout, acc, "a", MailState.DELETED)
    kept(db, layout, acc, "b", MailState.DELETED)
    kept(db, layout, acc, "c", MailState.DELETED)  # deleted in Gmail after the count was typed
    with pytest.raises(ForeverProblem, match=r"doesn't match the 3 mails picked"):
        forever(db, layout, acc, view="deleted", q="", confirm=2)
    assert db.scalar(select(MailMessage).where(MailMessage.subject == "a")) is not None


@pytest.mark.parametrize("view", ["all", "inbox", "archived", "suggested", "kept"])
def test_only_the_cleansync_views_take_it(db, layout, make_account, view):
    acc = make_account()
    keep(db, layout, acc, make_mail("In Gmail"))
    with pytest.raises(ForeverProblem, match="works in Offloaded and Deleted in Gmail"):
        forever(db, layout, acc, view=view, q="", confirm=1)


def test_an_empty_pick_never_falls_back_to_the_search(db, layout, make_account):
    acc = make_account()
    kept(db, layout, acc, "promo", MailState.OFFLOADED)
    with pytest.raises(ForeverProblem, match=r"^Pick mails or type a search\.$"):
        forever(db, layout, acc, view="offloaded", message_ids=[], q="promo", confirm=1)


def test_nothing_in_the_view_is_refused(db, layout, make_account):
    acc = make_account()
    row = keep(db, layout, acc, make_mail("In Gmail"))
    with pytest.raises(ForeverProblem, match=r"^None of these mails are in Offloaded\.$"):
        forever(db, layout, acc, view="offloaded", message_ids=[row.id], confirm=0)


def test_the_record_keeps_500_mails_and_says_how_many_more(db, layout, make_account):
    acc = make_account()
    for i in range(502):
        kept(db, layout, acc, f"mail {i}", MailState.OFFLOADED)
    assert forever(db, layout, acc, view="offloaded", q="", confirm=502).deleted == 502
    ev = db.scalar(select(Event).where(Event.kind == "mail_deleted_forever"))
    assert len(ev.data["mails"]) == 500 and ev.data["more"] == 2


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING, JobState.PAUSED])
def test_it_waits_for_a_restore(db, layout, make_account, state):
    acc = make_account()
    row = kept(db, layout, acc, "x", MailState.OFFLOADED)
    db.add(MailJob(account_id=acc.id, kind=MailJobKind.RESTORE, trigger="MANUAL", state=state))
    db.commit()
    with pytest.raises(ForeverProblem, match="A restore is running"):
        forever(db, layout, acc, view="offloaded", message_ids=[row.id], confirm=1)


def test_the_endpoint(auth_client, app, db, layout, make_account):
    acc = make_account()
    row = kept(db, layout, acc, "Promo", MailState.DELETED)
    url = "/api/mail/delete-forever"
    r = auth_client.post(url, json={"account_id": acc.id, "view": "deleted", "q": "", "confirm": 2})
    assert r.status_code == 400 and "doesn't match" in r.json()["detail"]
    app.state.mail.queue.waiting = lambda aid: [("restore", {})]
    r = auth_client.post(url, json={"account_id": acc.id, "view": "deleted", "q": "", "confirm": 1})
    assert r.status_code == 400 and "A restore is running" in r.json()["detail"]
    app.state.mail.queue.waiting = lambda aid: []
    body = {"account_id": acc.id, "view": "deleted", "message_ids": [row.id], "confirm": 1}
    assert auth_client.post(url, json=body).json() == {"deleted": 1, "left": 0, "job_id": None}
    old = auth_client.post(
        "/api/mail/archive/delete-forever",
        json={"account_id": acc.id, "message_ids": [row.id], "confirm": 1},
    )
    assert old.status_code in (404, 405)


def test_it_waits_for_a_restore_not_started_yet(db, layout, make_account):
    acc = make_account()
    row = kept(db, layout, acc, "x", MailState.OFFLOADED)
    with pytest.raises(ForeverProblem, match="A restore is running"):
        forever(
            db,
            layout,
            acc,
            view="offloaded",
            message_ids=[row.id],
            confirm=1,
            restore_queued=lambda account_id: account_id == acc.id,
        )
    assert db.get(MailMessage, row.id) is not None


def test_a_finished_restore_does_not_block(db, layout, make_account):
    acc = make_account()
    row = kept(db, layout, acc, "x", MailState.OFFLOADED)
    db.add(
        MailJob(
            account_id=acc.id, kind=MailJobKind.RESTORE, trigger="MANUAL", state=JobState.SUCCESS
        )
    )
    db.commit()
    res = forever(db, layout, acc, view="offloaded", message_ids=[row.id], confirm=1)
    assert res.deleted == 1


def test_the_wrong_count_message_says_one_mail(db, layout, make_account):
    acc = make_account()
    row = kept(db, layout, acc, "x", MailState.OFFLOADED)
    with pytest.raises(ForeverProblem, match=r"doesn't match the 1 mail picked\."):
        forever(db, layout, acc, view="offloaded", message_ids=[row.id], confirm=5)


def test_a_file_that_cannot_be_removed_does_not_fail_the_delete(
    db, layout, make_account, monkeypatch, caplog
):
    acc = make_account()
    row = kept(db, layout, acc, "x", MailState.OFFLOADED)
    row_id = row.id

    def boom(self, missing_ok=False):
        raise PermissionError("denied")

    monkeypatch.setattr("pathlib.Path.unlink", boom)
    res = forever(db, layout, acc, view="offloaded", message_ids=[row_id], confirm=1)
    assert res.deleted == 1 and db.get(MailMessage, row_id) is None
    assert "denied" in caplog.text


async def test_a_mail_deleted_during_a_restore_is_counted_not_restored(
    auth_client, offload_box, monkeypatch
):
    box = offload_box
    await _offloaded(auth_client, box)
    row = box.row("Old photos")
    with box.app.state.session_factory() as s:
        s.get(MailMessage, row.id).state = MailState.OFFLOADED
        s.commit()
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
        view="offloaded",
        message_ids=[row.id],
        confirm=1,
        now=NOW,
    )
    assert res.deleted == 1
    db.expire_all()
    assert db.scalar(select(RuleDecision).where(RuleDecision.message_id == row.id)) is None
    assert db.scalar(select(RuleTodo).where(RuleTodo.message_id == row.id)) is None
    assert db.scalar(select(MailOffloadItem).where(MailOffloadItem.message_id == row.id)) is None
    assert auth_client.get(f"/api/mail/offloads/{off_id}").status_code == 200
