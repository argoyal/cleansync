import asyncio
import threading
import time
from datetime import timedelta

from sqlalchemy import select

from app.core.clock import utcnow
from app.models import MailMessage, RuleAccount, RuleDecision, RuleJob, RuleTodo
from app.models.enums import DecisionStatus, MailState, RuleJobState, RuleMode
from app.services.ai.client import AiUnreachable
from app.services.mail.store import eml_path
from app.services.rules.apply import BUDGET_WAIT
from app.services.rules.match import Choice
from app.services.rules.store import RuleIn, create_rule
from app.services.rules.worker import GAVE_UP, SortingWorker, enqueue_new, retry_after, set_sorting


class Pick:
    def __init__(self, rule_id=None, error=None):
        self.rule_id, self.error, self.calls = rule_id, error, 0

    async def choose(self, rules, mail, hints):
        self.calls += 1
        if self.error:
            raise self.error
        return Choice(self.rule_id if self.rule_id is not None else rules[0].id, "fits", "m", 3)


def worker(gbox, chooser):
    return SortingWorker(
        session_factory=gbox.sf, layout=gbox.layout, chooser=lambda: chooser, applier=gbox.applier
    )


def queue(gbox, *rows):
    with gbox.sf() as db:
        set_sorting(db, gbox.acc.id, True)
        for r in rows:
            enqueue_new(db, gbox.acc.id, r)
        db.commit()


def decisions(gbox):
    with gbox.sf() as db:
        return list(db.scalars(select(RuleDecision).order_by(RuleDecision.id)))


def todos(gbox):
    with gbox.sf() as db:
        return list(db.scalars(select(RuleTodo)))


def test_retry_after_grows_and_caps():
    assert [retry_after(n) for n in (1, 2, 3)] == [timedelta(minutes=m) for m in (1, 2, 4)]
    assert retry_after(20) == timedelta(minutes=30)


def test_enqueue_only_when_sorting_is_on(gbox):
    row = gbox.add("Hi")
    with gbox.sf() as db:
        enqueue_new(db, gbox.acc.id, row)
        db.commit()
    assert todos(gbox) == []


async def test_suggest_mode_leaves_gmail_alone(gbox):
    with gbox.sf() as db:
        create_rule(
            db, gbox.acc.id, RuleIn(name="N", description="d", actions=[{"kind": "archive"}]), "a"
        )
    row = gbox.add("Digest")
    queue(gbox, row)
    assert await worker(gbox, Pick()).run_once(gbox.acc.id) == 1
    (d,) = decisions(gbox)
    assert d.status == "suggested" and d.rule_name == "N" and d.labels_seen == ["INBOX"]
    assert "INBOX" in gbox.fake.messages[row.provider_id].labels and todos(gbox) == []


async def test_automatic_mode_applies_at_once(gbox):
    with gbox.sf() as db:
        create_rule(
            db,
            gbox.acc.id,
            RuleIn(
                name="N",
                description="d",
                actions=[{"kind": "archive"}],
                mode=RuleMode.AUTOMATIC,
            ),
            "a",
        )
    row = gbox.add("Digest")
    queue(gbox, row)
    await worker(gbox, Pick()).run_once(gbox.acc.id)
    assert decisions(gbox)[0].status == "applied"
    assert "INBOX" not in gbox.fake.messages[row.provider_id].labels


async def test_unreachable_model_keeps_the_mail_waiting_and_marks_the_mailbox(gbox):
    with gbox.sf() as db:
        create_rule(
            db, gbox.acc.id, RuleIn(name="N", description="d", actions=[{"kind": "archive"}]), "a"
        )
    a, b = gbox.add("One"), gbox.add("Two")
    queue(gbox, a, b)
    pick = Pick(error=AiUnreachable("Couldn't reach the model."))
    await worker(gbox, pick).run_once(gbox.acc.id)
    assert decisions(gbox) == [] and pick.calls == 1  # stops the batch at the first failure
    t = next(t for t in todos(gbox) if t.message_id == a.id)
    assert t.attempts == 1 and "Couldn't reach" in t.last_error
    with gbox.sf() as db:
        assert db.get(RuleAccount, gbox.acc.id).failing_since is not None


async def test_unreadable_copy_waits_with_a_reason_and_others_go_on(gbox):
    with gbox.sf() as db:
        create_rule(
            db, gbox.acc.id, RuleIn(name="N", description="d", actions=[{"kind": "archive"}]), "a"
        )
    bad, good = gbox.add("Bad"), gbox.add("Good")
    eml_path(gbox.layout, gbox.acc.email, bad.file_path).unlink()
    queue(gbox, bad, good)
    await worker(gbox, Pick()).run_once(gbox.acc.id)
    assert [d.message_id for d in decisions(gbox)] == [good.id]
    (t,) = todos(gbox)
    assert t.message_id == bad.id and "missing" in t.last_error


async def test_no_rule_is_logged_when_there_are_no_rules(gbox):
    row = gbox.add("Hi")
    queue(gbox, row)
    await worker(gbox, Pick()).run_once(gbox.acc.id)  # no rules at all
    assert decisions(gbox)[0].status == DecisionStatus.NO_RULE


async def test_backup_keep_up_enqueues_new_inbox_mail(box):
    from app.services.rules.worker import enqueue_new as hook

    box.backup.on_kept = hook
    box.add(1)
    await box.run()  # first backup: never sorted
    with box.sf() as db:
        set_sorting(db, box.acc.id, True)
        db.commit()
    box.add(1)
    box.add(1, labels=("SENT",))
    await box.run()  # keep-up
    with box.sf() as db:
        assert len(list(db.scalars(select(RuleTodo)))) == 1


def _rule(gbox, **kw):
    with gbox.sf() as db:
        create_rule(
            db,
            gbox.acc.id,
            RuleIn(name="N", description="d", actions=[{"kind": "archive"}], **kw),
            "a",
        )


async def test_one_failing_mail_does_not_block_the_next(gbox):
    _rule(gbox)
    a, b = gbox.add("One"), gbox.add("Two")
    queue(gbox, a, b)

    class Flaky(Pick):
        async def choose(self, rules, mail, hints):
            if self.calls == 0:
                self.calls += 1
                raise RuntimeError("boom")
            return await super().choose(rules, mail, hints)

    await worker(gbox, Flaky()).run_once(gbox.acc.id)
    assert [d.message_id for d in decisions(gbox)] == [b.id]
    (t,) = todos(gbox)
    assert t.message_id == a.id and t.last_error.startswith("Something went wrong")


async def test_loop_survives_a_scan_error_and_still_sorts(gbox):
    _rule(gbox)
    queue(gbox, gbox.add("One"))
    w = SortingWorker(
        session_factory=gbox.sf,
        layout=gbox.layout,
        chooser=lambda: Pick(),
        applier=gbox.applier,
        idle_s=0.05,
    )
    real, calls = w._accounts_with_work, {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db hiccup")
        return real()

    w._accounts_with_work = flaky
    w.start()
    try:
        for _ in range(100):
            if decisions(gbox):
                break
            w.poke()
            await asyncio.sleep(0.05)
    finally:
        await w.stop()
    assert len(decisions(gbox)) == 1


async def test_a_poke_from_another_thread_wakes_the_waiting_loop(gbox):
    """start_preview pokes from a sync endpoint's worker thread. Setting the asyncio.Event
    there doesn't wake a loop that is idle, so the poke must go through the loop."""
    _rule(gbox)
    chosen = asyncio.Event()

    class Telling(Pick):
        async def choose(self, rules, mail, hints):
            chosen.set()
            return await super().choose(rules, mail, hints)

    w = SortingWorker(
        session_factory=gbox.sf,
        layout=gbox.layout,
        chooser=lambda: Telling(),
        applier=gbox.applier,
        idle_s=600,
    )
    w.start()
    try:
        await asyncio.sleep(0.1)  # nothing to do: the loop is waiting for a poke
        queue(gbox, gbox.add("One"))
        # A plain thread that pokes once the loop is asleep: nothing else wakes it meanwhile.
        poker = threading.Thread(target=lambda: (time.sleep(0.2), w.poke()))
        poker.start()
        await asyncio.wait_for(chosen.wait(), 2)
        poker.join()
    finally:
        await w.stop()


async def test_waiting_decision_is_listed_and_retried_without_new_mail(gbox):
    _rule(gbox)
    row = gbox.add("One")
    queue(gbox, row)
    await worker(gbox, Pick()).run_once(gbox.acc.id)
    with gbox.sf() as db:
        d = db.scalars(select(RuleDecision)).one()
        d.reason = BUDGET_WAIT
        db.commit()
    w = worker(gbox, Pick())
    assert w._accounts_with_work() == [gbox.acc.id]
    await w.run_once(gbox.acc.id)
    assert decisions(gbox)[0].status == "applied"


def test_turning_sorting_off_drops_new_mail_to_do_rows_but_keeps_jobs(gbox):
    a, b = gbox.add("One"), gbox.add("Two")
    queue(gbox, a)
    with gbox.sf() as db:
        job = RuleJob(account_id=gbox.acc.id, total=1)
        db.add(job)
        db.flush()
        db.add(RuleTodo(account_id=gbox.acc.id, message_id=b.id, job_id=job.id))
        set_sorting(db, gbox.acc.id, False)
        db.commit()
    assert [t.message_id for t in todos(gbox)] == [b.id]


async def test_new_mail_row_is_dropped_when_sorting_is_off(gbox):
    _rule(gbox)
    row = gbox.add("One")
    queue(gbox, row)
    with gbox.sf() as db:
        db.get(RuleAccount, gbox.acc.id).sorting_enabled = False
        db.commit()
    await worker(gbox, Pick()).run_once(gbox.acc.id)
    assert decisions(gbox) == [] and todos(gbox) == []


async def test_unreadable_copy_is_given_up_after_ten_tries(gbox):
    _rule(gbox)
    bad = gbox.add("Bad")
    eml_path(gbox.layout, gbox.acc.email, bad.file_path).unlink()
    queue(gbox, bad)
    with gbox.sf() as db:
        db.get(RuleTodo, (gbox.acc.id, bad.id)).attempts = 9
        db.commit()
    await worker(gbox, Pick()).run_once(gbox.acc.id)
    (d,) = decisions(gbox)
    assert d.status == DecisionStatus.FAILED and d.how == "none" and "can't be read" in d.reason
    assert todos(gbox) == []


async def test_changes_are_announced_even_when_nothing_was_decided(gbox):
    from app.services.events.bus import EventBus

    _rule(gbox)
    queue(gbox, gbox.add("One"))
    bus, seen = EventBus(), []
    bus.publish = lambda topic, data: seen.append((topic, data))
    w = SortingWorker(
        session_factory=gbox.sf,
        layout=gbox.layout,
        chooser=lambda: Pick(error=AiUnreachable("Couldn't reach the model.")),
        applier=gbox.applier,
        bus=bus,
    )
    assert await w.run_once(gbox.acc.id) == 0
    assert seen == [("rules", {"account_id": gbox.acc.id})]


async def test_mail_no_longer_kept_is_dropped(gbox):
    _rule(gbox)
    row = gbox.add("One")
    queue(gbox, row)
    with gbox.sf() as db:
        db.get(MailMessage, row.id).state = MailState.LOCAL_ARCHIVE
        db.commit()
    pick = Pick()
    await worker(gbox, pick).run_once(gbox.acc.id)
    assert pick.calls == 0 and decisions(gbox) == [] and todos(gbox) == []


async def test_mail_the_owner_moved_back_is_dropped(gbox):
    from datetime import UTC, datetime

    _rule(gbox)
    row = gbox.add("One")
    with gbox.sf() as db:
        db.add(
            RuleDecision(
                account_id=gbox.acc.id,
                message_id=row.id,
                status=DecisionStatus.APPLIED,
                moved_back_at=datetime.now(UTC),
            )
        )
        db.commit()
    queue(gbox, row)
    pick = Pick()
    await worker(gbox, pick).run_once(gbox.acc.id)
    assert pick.calls == 0 and todos(gbox) == []


async def test_job_mail_is_planned_and_finishes_the_preview(gbox):
    _rule(gbox)
    row = gbox.add("One")
    with gbox.sf() as db:
        job = RuleJob(account_id=gbox.acc.id, total=1)
        db.add(job)
        db.flush()
        job_id = job.id
        db.add(RuleTodo(account_id=gbox.acc.id, message_id=row.id, job_id=job_id))
        db.commit()
    await worker(gbox, Pick()).run_once(gbox.acc.id)  # sorting is off: jobs still run
    (d,) = decisions(gbox)
    assert d.status == DecisionStatus.PLANNED and d.job_id == job_id
    with gbox.sf() as db:
        job = db.get(RuleJob, job_id)
        assert job.decided == 1 and job.state == RuleJobState.PREVIEWED


async def test_failing_flag_clears_after_a_success(gbox):
    _rule(gbox)
    queue(gbox, gbox.add("One"))
    with gbox.sf() as db:
        db.get(RuleAccount, gbox.acc.id).failing_since = utcnow()
        db.commit()
    await worker(gbox, Pick()).run_once(gbox.acc.id)
    with gbox.sf() as db:
        assert db.get(RuleAccount, gbox.acc.id).failing_since is None


def _job_with(gbox, *rows):
    with gbox.sf() as db:
        job = RuleJob(account_id=gbox.acc.id, total=len(rows))
        db.add(job)
        db.flush()
        for r in rows:
            db.add(RuleTodo(account_id=gbox.acc.id, message_id=r.id, job_id=job.id))
        db.commit()
        return job.id


def _job(gbox, job_id):
    with gbox.sf() as db:
        j = db.get(RuleJob, job_id)
        return j.decided, j.total, j.state


async def test_job_with_an_unreadable_mail_still_finishes_its_preview(gbox):
    _rule(gbox)
    bad, good = gbox.add("Bad"), gbox.add("Good")
    eml_path(gbox.layout, gbox.acc.email, bad.file_path).unlink()
    jid = _job_with(gbox, bad, good)
    with gbox.sf() as db:
        db.get(RuleTodo, (gbox.acc.id, bad.id)).attempts = 9
        db.commit()
    await worker(gbox, Pick()).run_once(gbox.acc.id)
    assert _job(gbox, jid) == (2, 2, RuleJobState.PREVIEWED)


async def test_job_row_whose_mail_left_the_mailbox_is_counted(gbox):
    _rule(gbox)
    row = gbox.add("One")
    jid = _job_with(gbox, row)
    with gbox.sf() as db:
        db.get(MailMessage, row.id).state = MailState.LOCAL_ARCHIVE
        db.commit()
    await worker(gbox, Pick()).run_once(gbox.acc.id)
    assert todos(gbox) == [] and _job(gbox, jid) == (1, 1, RuleJobState.PREVIEWED)


async def test_job_mail_that_keeps_raising_is_given_up_and_counted(gbox):
    _rule(gbox)
    row = gbox.add("One")
    jid = _job_with(gbox, row)
    with gbox.sf() as db:
        db.get(RuleTodo, (gbox.acc.id, row.id)).attempts = 9
        db.commit()
    await worker(gbox, Pick(error=RuntimeError("boom"))).run_once(gbox.acc.id)
    (d,) = decisions(gbox)
    assert d.status == DecisionStatus.FAILED and d.reason == GAVE_UP
    assert todos(gbox) == [] and _job(gbox, jid) == (1, 1, RuleJobState.PREVIEWED)


async def test_the_mail_file_is_read_off_the_event_loop(gbox, monkeypatch):
    from app.services.rules import worker as worker_mod

    with gbox.sf() as db:
        create_rule(
            db, gbox.acc.id, RuleIn(name="N", description="d", actions=[{"kind": "star"}]), "a"
        )
    row = gbox.add("Hi")
    queue(gbox, row)
    real, on_loop = worker_mod.mail_for_model, []

    def spy(*a, **kw):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*a, **kw)

    monkeypatch.setattr(worker_mod, "mail_for_model", spy)
    assert await worker(gbox, Pick()).run_once(gbox.acc.id) == 1
    assert on_loop == [False]


async def test_keep_up_to_do_row_goes_in_the_same_transaction_as_the_mail(box):
    """Merge of 0.2.1: each mail is saved in one short transaction off the event loop; the
    sorting to-do row must still be written inside it, before the commit."""
    seen = []

    def hook(s, account_id, row):
        with box.sf() as other:  # a second connection sees only committed rows
            visible = other.scalar(select(MailMessage).where(MailMessage.id == row.id))
        seen.append((s.in_transaction(), visible is None, threading.current_thread().name))
        enqueue_new(s, account_id, row)

    box.backup.on_kept = hook
    box.add(1)
    await box.run()  # first backup: never sorted
    assert seen == []
    with box.sf() as db:
        set_sorting(db, box.acc.id, True)
        db.commit()
    box.add(1)
    box.add(1, labels=("SENT",))
    await box.run()  # keep-up
    assert len(seen) == 1  # only the new INBOX mail
    in_tx, not_yet_committed, thread = seen[0]
    assert in_tx and not_yet_committed
    assert thread != threading.main_thread().name  # run off the event loop
    with box.sf() as db:
        (todo,) = db.scalars(select(RuleTodo))
        assert db.get(MailMessage, todo.message_id) is not None


async def test_an_error_before_the_mail_is_read_counts_a_try_and_is_given_up(gbox, monkeypatch):
    from app.services.rules import worker as worker_mod
    from app.services.rules.worker import SOMETHING_WRONG

    def broken(db, message_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_mod, "moved_back", broken)
    _rule(gbox)
    row = gbox.add("One")
    jid = _job_with(gbox, row)
    w = worker(gbox, Pick())
    assert await w.run_once(gbox.acc.id) == 0
    (todo,) = todos(gbox)
    assert (todo.attempts, todo.last_error) == (1, SOMETHING_WRONG)
    with gbox.sf() as db:
        db.get(RuleTodo, (gbox.acc.id, row.id)).attempts = 9
        db.get(RuleTodo, (gbox.acc.id, row.id)).next_try_at = utcnow()
        db.commit()
    assert await w.run_once(gbox.acc.id) == 1
    (d,) = decisions(gbox)
    assert d.status == DecisionStatus.FAILED and d.reason == GAVE_UP and d.job_id == jid
    assert todos(gbox) == [] and _job(gbox, jid) == (1, 1, RuleJobState.PREVIEWED)
