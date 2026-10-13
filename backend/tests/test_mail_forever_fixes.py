"""Final review fixes for Delete forever jobs (round 3): a job that loses an item mid-way, a
pause partway through one-by-one deletes, Stop then pause, and wording that is its own."""

from sqlalchemy import select

from app.models import Account, Event, MailMessage, MailOffload, MailOffloadItem
from app.models.enums import AccountStatus, MailOffloadKind, MailOffloadState, MailState
from app.services.google.client import RateLimited
from app.services.mail import offload as offload_mod
from app.services.mail import offload_plan
from app.services.mail.adapter import MailApiError
from app.services.mail.offload import STOPPED_MSG
from app.services.mail.offload_plan import recover_mail_offloads
from tests.test_mail_bulk_offload import Recording, _use, allow_permanent
from tests.test_mail_forever_gmail import _forever
from tests.test_mail_offload_run import OLD, _start


def _no_offload_words(text: str) -> None:
    """A Delete forever job's words never talk about an offload."""
    assert "offload" not in text.lower(), text


async def test_an_item_that_vanished_mid_job_is_skipped_not_a_crash(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    job_id = _forever(auth_client, box, 3, view="all", q="older_than:1y").json()["job_id"]
    real_stopped = box.mail.stopped

    async def stopped_then_lose_an_item():
        # Right after the run listed its items, a rule approval takes one of them away.
        with box.app.state.session_factory() as other:
            item = other.scalars(
                select(MailOffloadItem).where(MailOffloadItem.offload_id == job_id)
            ).first()
            other.delete(item)
            other.commit()
        box.mail.stopped = real_stopped
        return await real_stopped()

    box.mail.stopped = stopped_then_lose_an_item
    await box.run_queued()
    off = db.get(MailOffload, job_id)
    db.refresh(off)
    assert off.state == MailOffloadState.DONE_WITH_SKIPS, off.reason
    assert off.reason is None and (off.done, off.skipped) == (2, 1)


class DeleteOneThenPause(Recording):
    """batchDelete is refused, so the job goes one mail at a time; the second one is paused."""

    def __init__(self, inner, error):
        super().__init__(inner)
        self.deleted, self.error = 0, error

    async def batch_delete(self, ids):
        raise MailApiError("Gmail said no", 400)

    async def delete(self, pid):
        self.deleted += 1
        if self.deleted == 2:
            raise self.error
        await self.inner.delete(pid)


async def test_a_pause_part_way_through_one_by_one_removes_the_copies_of_what_gmail_removed(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    job_id = _forever(auth_client, box, 3, view="all", q="older_than:1y").json()["job_id"]
    _use(box, DeleteOneThenPause(box.mail.adapters.adapter, RateLimited()))
    await box.run_queued()
    off = db.get(MailOffload, job_id)
    db.refresh(off)
    assert off.state == MailOffloadState.WAITING
    assert off.done == 1 and off.removed == 1  # counted now, not on the next run
    gone = [s for s in OLD if box.row(s) is None]
    assert len(gone) == 1  # its copy is already removed too


class StopThenPause(Recording):
    """The owner asks to stop during a batch, and Gmail also asks to slow down."""

    def __init__(self, inner, stop):
        super().__init__(inner)
        self.stop = stop

    async def batch_delete(self, ids):
        self.stop()
        raise RateLimited()

    async def batch_trash(self, ids):
        self.stop()
        raise RateLimited()


async def test_stop_then_pause_ends_a_delete_forever_job_as_stopped(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    job_id = _forever(auth_client, box, 3, view="all", q="older_than:1y").json()["job_id"]
    _use(box, StopThenPause(box.mail.adapters.adapter, lambda: box.offloads.stop(job_id)))
    await box.run_queued()
    off = db.get(MailOffload, job_id)
    db.refresh(off)
    assert off.state == MailOffloadState.FAILED
    assert off.reason == offload_plan.FOREVER_STOPPED_MSG and off.finished_at is not None
    _no_offload_words(off.reason)


async def test_stop_then_pause_ends_an_offload_as_stopped(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    _use(box, StopThenPause(box.mail.adapters.adapter, lambda: box.offloads.stop(off_id)))
    await box.run_queued()
    off = db.get(MailOffload, off_id)
    db.refresh(off)
    assert off.state == MailOffloadState.FAILED and off.reason == STOPPED_MSG
    assert off.finished_at is not None


async def test_refusals_in_a_row_use_delete_forever_words(
    auth_client, offload_box, db, monkeypatch
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    monkeypatch.setattr(offload_mod, "REFUSED_IN_A_ROW", 2)
    job_id = _forever(auth_client, box, 3, view="all", q="older_than:1y").json()["job_id"]
    box.fake.fail("POST", r"/batchDelete$", 400)
    box.fake.fail("DELETE", r"/messages/", 400, times=10)
    await box.run_queued()
    off = db.get(MailOffload, job_id)
    db.refresh(off)
    assert off.state == MailOffloadState.FAILED
    assert off.reason.startswith("Gmail refused 2 mails in a row (")
    assert off.reason.endswith("Start a new Delete forever to go on.")
    assert offload_mod.FOREVER_REST in off.reason
    _no_offload_words(off.reason)


async def test_a_mail_no_longer_in_gmail_says_its_copy_is_kept(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    job_id = _forever(auth_client, box, 3, view="all", q="older_than:1y").json()["job_id"]
    row = box.row("Old photos")
    # something else took it out of Gmail before the run
    db.get(MailMessage, row.id).state = MailState.OFFLOADED
    db.commit()
    await box.run_queued()
    item = db.scalars(
        select(MailOffloadItem).where(
            MailOffloadItem.offload_id == job_id, MailOffloadItem.message_id == row.id
        )
    ).one()
    assert item.reason == offload_mod.SKIP_FOREVER_NOT_IN_GMAIL
    assert "copy" in item.reason and "kept" in item.reason
    _no_offload_words(item.reason)


def _running_forever(db, acc, **kw):
    off = MailOffload(
        account_id=acc.id,
        filter_text="x",
        total=3,
        state=MailOffloadState.RUNNING,
        kind=MailOffloadKind.DELETE_FOREVER,
        **kw,
    )
    db.add(off)
    db.commit()
    return off


def test_a_restart_gives_a_delete_forever_job_its_own_words(db, make_account):
    acc = make_account()
    going = _running_forever(db, acc)
    stopping = _running_forever(db, acc, stop_asked=True)
    recover_mail_offloads(db)
    db.expire_all()
    assert going.reason == offload_plan.FOREVER_RESTARTED_MSG
    assert stopping.reason == offload_plan.FOREVER_STOPPED_MSG
    _no_offload_words(going.reason)
    _no_offload_words(stopping.reason)
    assert stopping.reason.startswith("Stopped by you.")


async def test_a_delete_forever_job_that_cant_start_says_delete_forever(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    job_id = _forever(auth_client, box, 3, view="all", q="older_than:1y").json()["job_id"]
    acc = db.get(Account, box.acc.id)
    acc.status = AccountStatus.NEEDS_REAUTH
    db.commit()
    await box.run_queued()
    off = db.get(MailOffload, job_id)
    db.refresh(off)
    assert off.state == MailOffloadState.FAILED
    _no_offload_words(off.reason)
    ev = db.scalars(select(Event).where(Event.kind == "mail_offload")).all()[-1]
    assert ev.message.startswith(f"Delete forever for {box.acc.email}:")
