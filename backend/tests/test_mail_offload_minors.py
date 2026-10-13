"""0.5.1 minors in mail offload: what a skipped, stopped or restarted offload says, and the
Stopped flag the page reads."""

from datetime import timedelta

from app.core.clock import utcnow
from app.models import MailOffload, MailOffloadItem
from app.models.enums import MailOffloadKind, MailOffloadState, MailState
from app.services.mail import offload as offload_mod
from app.services.mail.adapter import MailUnreachable
from app.services.mail.offload import (
    FOREVER_STOPPED_MSG,
    FOREVER_STOPPED_UNKNOWN_MSG,
    SKIP_BAD_COPY_OUT,
    STOPPED_MSG,
    STOPPED_UNKNOWN_MSG,
    offload_out,
)
from app.services.mail.offload_plan import recover_mail_offloads
from app.services.mail.store import eml_path
from tests.test_mail_bulk_offload import LostAnswer, _use, allow_permanent
from tests.test_mail_offload_run import _start


async def _no_nap(_s):
    return None


async def test_a_resent_mail_whose_copy_fails_says_it_is_already_out_of_gmail(
    auth_client, offload_box, db
):
    """Gmail did the batch but CleanSync never recorded it (state "deleted in Gmail"). If the
    copy then fails its check, the mail did NOT stay in Gmail."""
    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    _use(box, LostAnswer(box.mail.adapters.adapter))
    await box.run_queued()
    await box.backup()  # a check sees them in the trash: Deleted in Gmail
    bad = box.row("Old photos")
    assert bad.state == MailState.DELETED
    path = eml_path(box.app.state.layout, box.acc.email, bad.file_path)
    path.write_bytes(path.read_bytes()[:-1] + b"!")  # same size, other contents
    await box.mail.tick()
    await box.run_queued()
    item = db.query(MailOffloadItem).filter_by(message_id=bad.id).one()
    assert item.reason == SKIP_BAD_COPY_OUT.format(problem="the file's contents changed")
    assert "stayed in Gmail" not in item.reason
    assert "already out of Gmail" in item.reason and "copy is kept" in item.reason
    assert box.row("Old photos").state == MailState.DELETED  # the copy is kept, as it was


async def test_a_stop_while_gmail_did_not_answer_says_the_batch_may_be_out_of_gmail(
    auth_client, offload_box, db
):
    box = offload_box
    box.offloads.sleep = _no_nap
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    box.fake.fail("POST", r"/batchModify$", 503, times=3)
    await box.run_queued()
    off = db.get(MailOffload, off_id)
    db.refresh(off)
    assert off.state == MailOffloadState.WAITING and off.reason == offload_mod.OFFLOAD_NO_ANSWER_MSG
    assert auth_client.post(f"/api/mail/offloads/{off_id}/stop").status_code == 204
    db.refresh(off)
    assert off.state == MailOffloadState.FAILED and off.reason == STOPPED_UNKNOWN_MSG
    assert "may already be out of Gmail" in off.reason
    assert "Deleted in Gmail" in off.reason and "copies are kept" in off.reason
    assert "weren't" not in off.reason


class StopThenNoAnswer(LostAnswer):
    """The owner asks to stop during a batch, and Gmail never answers it."""

    def __init__(self, inner, stop):
        super().__init__(inner)
        self.stop = stop

    async def batch_trash(self, ids):
        self.stop()
        raise MailUnreachable("no answer")


async def test_a_stop_asked_in_a_batch_gmail_never_answered_says_the_same(
    auth_client, offload_box, db
):
    box = offload_box
    box.offloads.sleep = _no_nap
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    _use(box, StopThenNoAnswer(box.mail.adapters.adapter, lambda: box.offloads.stop(off_id)))
    await box.run_queued()
    off = db.get(MailOffload, off_id)
    db.refresh(off)
    assert off.state == MailOffloadState.FAILED and off.reason == STOPPED_UNKNOWN_MSG


def test_a_restart_that_finds_a_stop_asked_says_the_batch_may_be_out_of_gmail(db, make_account):
    acc = make_account()
    rows = []
    for kind in (MailOffloadKind.OFFLOAD, MailOffloadKind.DELETE_FOREVER):
        off = MailOffload(
            account_id=acc.id,
            filter_text="x",
            total=3,
            state=MailOffloadState.RUNNING,
            stop_asked=True,
            kind=kind,
        )
        db.add(off)
        rows.append(off)
    db.commit()
    recover_mail_offloads(db)
    db.expire_all()
    assert rows[0].reason == STOPPED_UNKNOWN_MSG
    assert rows[1].reason == FOREVER_STOPPED_UNKNOWN_MSG


async def test_a_plain_stop_still_says_what_was_not_reached_stayed_in_gmail(
    auth_client, offload_box, db
):
    box = offload_box
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    auth_client.post(f"/api/mail/offloads/{off_id}/stop")
    assert db.get(MailOffload, off_id).reason == STOPPED_MSG


async def test_the_offload_says_plainly_when_the_owner_stopped_it(auth_client, offload_box, db):
    """`stopped` on the offload (list and detail): the page doesn't match on text."""
    box = offload_box
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    assert auth_client.get(f"/api/mail/offloads/{off_id}").json()["stopped"] is False
    auth_client.post(f"/api/mail/offloads/{off_id}/stop")
    detail = auth_client.get(f"/api/mail/offloads/{off_id}").json()
    assert detail["state"] == "FAILED" and detail["stopped"] is True
    listed = auth_client.get("/api/mail/offloads", params={"account_id": box.acc.id}).json()
    assert [o["stopped"] for o in listed] == [True]
    # A failure that is not a stop is not "stopped".
    other = MailOffload(
        account_id=box.acc.id,
        filter_text="x",
        total=1,
        state=MailOffloadState.FAILED,
        reason="Reconnect this Google account.",
    )
    db.add(other)
    db.commit()
    assert offload_out(db, other, utcnow())["stopped"] is False


async def test_a_stopped_delete_forever_job_is_stopped_too(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    done = MailOffload(
        account_id=box.acc.id,
        filter_text="x",
        total=1,
        state=MailOffloadState.FAILED,
        reason=FOREVER_STOPPED_MSG,
        kind=MailOffloadKind.DELETE_FOREVER,
        created_at=utcnow() - timedelta(days=1),
    )
    db.add(done)
    db.commit()
    assert offload_out(db, done, utcnow())["stopped"] is True


async def test_offload_on_one_mail_that_is_not_in_gmail_is_refused_without_a_chip(
    auth_client, offload_box, db
):
    from app.models import RuleDecision
    from app.services.rules.apply import NOT_IN_MAIL_MSG

    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    await box.run_queued()
    gone = box.row("Old statement")
    assert gone.state == MailState.OFFLOADED
    r = auth_client.post(f"/api/mail/messages/{gone.id}/offload", json={"account_id": box.acc.id})
    assert r.status_code == 200 and r.json() == {"offloaded": False, "reason": NOT_IN_MAIL_MSG}
    assert db.query(RuleDecision).count() == 0
    assert box.row("Old statement").state == MailState.OFFLOADED
