import asyncio

from sqlalchemy import select

from app.models import MailJob
from app.models.enums import JobState, MailJobKind
from app.services.events.bus import EventBus
from app.services.mail.jobs import (
    INTERRUPTED_MAIL_MSG,
    SETTLED_MAIL_MSG,
    finish_mail_job,
    recover_mail_jobs,
    settle_mail_jobs,
    start_mail_job,
)
from app.services.mail.queue import MailQueue


async def _never_stopped():
    return False


async def test_jobs_for_one_account_run_one_at_a_time_in_the_order_asked():
    seen, active = [], set()

    async def handler(account_id, action, args, trigger):
        assert account_id not in active
        active.add(account_id)
        await asyncio.sleep(0.01)
        seen.append((account_id, action, args, trigger))
        active.discard(account_id)

    q = MailQueue(handler, is_stopped=_never_stopped)
    assert q.request(1, "backup") == "queued"
    await asyncio.sleep(0)  # the first job starts; the next ones queue behind it
    assert q.request(1, "offload", {"offload_id": 4}, trigger="MANUAL") == "queued"
    assert q.request(1, "backup", trigger="MANUAL") == "queued"  # behind the offload
    assert q.request(1, "backup") == "already_queued"  # the same job is waiting already
    await q.wait_idle()
    assert [s[1] for s in seen] == ["backup", "offload", "backup"]
    assert seen[1][2] == {"offload_id": 4}
    assert not q.is_busy(1)


async def test_two_accounts_run_side_by_side():
    gate = asyncio.Event()
    running = set()

    async def handler(account_id, action, args, trigger):
        running.add(account_id)
        await gate.wait()

    q = MailQueue(handler, max_parallel=2, is_stopped=_never_stopped)
    q.request(1, "backup")
    q.request(2, "backup")
    await asyncio.sleep(0.01)
    assert running == {1, 2}
    gate.set()
    await q.wait_idle()


async def test_a_failing_or_cancelled_mail_job_always_releases_its_busy_flag():
    settled: list[int] = []
    gate = asyncio.Event()

    async def handler(account_id, action, args, trigger):
        if account_id == 1:
            raise RuntimeError("boom")
        if account_id == 2:
            raise asyncio.CancelledError  # a timeout inside the job
        await gate.wait()  # account 3 hangs until stopped

    q = MailQueue(handler, is_stopped=_never_stopped, settle=settled.append)
    finished: list[int] = []
    q.add_finish_listener(finished.append)
    for a in (1, 2, 3):
        q.request(a, "backup")
    q.request(3, "offload", {"offload_id": 1})  # waits behind the hanging job
    await asyncio.sleep(0.02)
    assert not q.is_busy(1) and not q.is_busy(2)
    assert q.is_running(3) and q.waiting(3) == [("offload", {"offload_id": 1})]
    await q.stop()
    assert not q.is_busy(3) and q.waiting(3) == []
    # Cut off by stop(): not a failure. Account 3's job is left for startup recovery.
    assert sorted(settled) == [1, 2]
    assert finished == [1, 2]  # no finish listener for the job stop() cut off
    assert q.request(1, "backup") == "disabled"  # nothing new starts while stopping


async def test_nothing_runs_while_the_disk_is_missing():
    ran = []

    async def handler(account_id, action, args, trigger):
        ran.append(account_id)

    async def stopped():
        return True

    q = MailQueue(handler, is_stopped=stopped, settle=ran.append)
    q.request(1, "backup")
    q.request(1, "offload", {"offload_id": 2})
    await q.wait_idle()
    assert ran == []  # neither job ran, and nothing needed tidying
    assert not q.is_busy(1)


def test_mail_jobs_are_recorded_settled_and_recovered(db, layout, session_factory, make_account):
    acc = make_account()
    bus = EventBus()
    job = start_mail_job(db, layout, bus, acc.id, MailJobKind.BACKUP, "MANUAL")
    assert job.state == JobState.RUNNING and layout.mail_job_log(job.id).exists()
    assert settle_mail_jobs(session_factory, bus, acc.id) is True
    db.expire_all()
    assert db.get(MailJob, job.id).state == JobState.FAILED
    assert db.get(MailJob, job.id).message == SETTLED_MAIL_MSG
    assert settle_mail_jobs(session_factory, bus, acc.id) is False
    second = start_mail_job(db, layout, bus, acc.id, MailJobKind.CHECK, "SCHEDULE")
    done = start_mail_job(db, layout, bus, acc.id, MailJobKind.CHECK, "SCHEDULE")
    finish_mail_job(db, bus, done.id, JobState.SUCCESS, "Nothing new", done=0, bytes_=0)
    assert recover_mail_jobs(db) == 1
    db.expire_all()
    assert db.get(MailJob, second.id).state == JobState.INTERRUPTED
    assert db.get(MailJob, second.id).message == INTERRUPTED_MAIL_MSG
    assert db.scalars(select(MailJob.state).where(MailJob.id == done.id)).one() == JobState.SUCCESS
