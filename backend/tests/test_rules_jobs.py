import asyncio
import threading

import pytest
from sqlalchemy import select, update

from app.models import MailMessage, Rule, RuleDecision, RuleJob, RuleTodo
from app.models.enums import DecisionStatus, RuleJobState
from app.services.ai.client import AiUnreachable
from app.services.rules.jobs import RuleJobProblem, RuleJobs, RunScope
from app.services.rules.match import Choice
from app.services.rules.store import RuleIn, create_rule, update_rule
from app.services.rules.worker import SortingWorker

ARCHIVE = RuleIn(name="News", description="digests", actions=[{"kind": "archive"}])


class First:
    async def choose(self, rules, mail, hints):
        return Choice(rules[0].id, "fits")


def setup(gbox):
    worker = SortingWorker(
        session_factory=gbox.sf, layout=gbox.layout, chooser=lambda: First(), applier=gbox.applier
    )
    return worker, RuleJobs(gbox.sf, gbox.applier, worker)


INBOX = RunScope(inbox=True)


def news_rule(gbox) -> int:
    """The News rule's id, made the first time a test needs it."""
    with gbox.sf() as db:
        rid = db.scalar(select(Rule.id).where(Rule.account_id == gbox.acc.id, Rule.name == "News"))
        return rid if rid is not None else create_rule(db, gbox.acc.id, ARCHIVE, "a").id


def on_inbox(gbox, jobs) -> int:
    """A rule card's Run of News over the Inbox."""
    return jobs.start_preview(gbox.acc.id, rule_ids=[news_rule(gbox)], scope=INBOX)


async def preview(gbox, worker, jobs, scope=INBOX):
    jid = jobs.start_preview(gbox.acc.id, rule_ids=[news_rule(gbox)], scope=scope)
    while await worker.run_once(gbox.acc.id):
        pass
    return jid, jobs.preview(gbox.acc.id, jid)


async def test_preview_then_run_applies_without_a_typed_count_for_archive(gbox):
    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    rows = [gbox.add(f"D{i}") for i in range(3)]
    worker, jobs = setup(gbox)
    jid, p = await preview(gbox, worker, jobs)
    assert p.state == RuleJobState.PREVIEWED and p.planned == 3
    assert p.per_rule[0]["rule"] == "News" and p.per_rule[0]["count"] == 3
    assert all("INBOX" in gbox.fake.messages[r.provider_id].labels for r in rows)  # nothing yet
    jobs.run(gbox.acc.id, jid)  # archive needs no typed count
    await jobs.wait(jid)
    assert jobs.preview(gbox.acc.id, jid).applied == 3
    assert all("INBOX" not in gbox.fake.messages[r.provider_id].labels for r in rows)


async def test_mail_changed_since_preview_is_skipped_and_listed(gbox):
    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    a = gbox.add("A")
    gbox.add("B")
    worker, jobs = setup(gbox)
    jid, _ = await preview(gbox, worker, jobs)
    with gbox.sf() as db:
        db.get(MailMessage, a.id).labels = ["INBOX", "STARRED"]  # the owner starred it
        db.commit()
    jobs.run(gbox.acc.id, jid, 2)
    await jobs.wait(jid)
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.applied, p.skipped) == (1, 1)
    with gbox.sf() as db:
        d = db.scalar(select(RuleDecision).where(RuleDecision.message_id == a.id))
        assert d.status == "skipped" and "changed since the preview" in d.reason


async def test_edited_rule_since_preview_is_skipped(gbox):
    with gbox.sf() as db:
        rule = create_rule(db, gbox.acc.id, ARCHIVE, "a")
        rid = rule.id
    a = gbox.add("A")
    worker, jobs = setup(gbox)
    jid, _ = await preview(gbox, worker, jobs)
    with gbox.sf() as db:
        update_rule(
            db,
            gbox.acc.id,
            rid,
            RuleIn(name="News", description="changed", actions=[{"kind": "archive"}]),
            "a",
        )
    jobs.run(gbox.acc.id, jid, 1)
    await jobs.wait(jid)
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.applied, p.skipped) == (0, 1)
    with gbox.sf() as db:
        d = db.scalar(select(RuleDecision).where(RuleDecision.message_id == a.id))
        assert "rule changed since the preview" in d.reason
    assert "INBOX" in gbox.fake.messages[a.provider_id].labels


async def test_nothing_to_do_and_too_much_are_refused(gbox, monkeypatch):
    worker, jobs = setup(gbox)
    with pytest.raises(RuleJobProblem, match="No mail"):
        on_inbox(gbox, jobs)
    gbox.add("x")
    monkeypatch.setattr("app.services.rules.jobs.MAX_JOB", 0)
    with pytest.raises(RuleJobProblem, match="narrow"):
        on_inbox(gbox, jobs)


async def test_stop_drops_remaining_work(gbox):
    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    gbox.add("x")
    worker, jobs = setup(gbox)
    jid = on_inbox(gbox, jobs)
    jobs.stop(gbox.acc.id, jid)
    assert await worker.run_once(gbox.acc.id) == 0
    assert jobs.preview(gbox.acc.id, jid).state == RuleJobState.STOPPED


async def test_run_waits_when_gmail_is_unreachable_then_goes_on(gbox, monkeypatch):
    from app.services.mail.adapter import MailUnreachable

    monkeypatch.setattr("app.services.rules.jobs.WAIT_SECONDS", 0)
    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    r = gbox.add("A")
    worker, jobs = setup(gbox)
    jid, _ = await preview(gbox, worker, jobs)
    real, calls = gbox.applier.apply, []

    async def flaky(did):
        calls.append(did)
        if len(calls) == 1:
            with gbox.sf() as db:
                db.get(RuleDecision, did).reason = "Gmail couldn't be reached; wait."
                db.commit()
            raise MailUnreachable("down")
        return await real(did)

    monkeypatch.setattr(gbox.applier, "apply", flaky)
    jobs.run(gbox.acc.id, jid, 1)
    await jobs.wait(jid)
    p = jobs.preview(gbox.acc.id, jid)
    assert p.applied == 1 and p.state == RuleJobState.DONE and len(calls) == 2
    assert "INBOX" not in gbox.fake.messages[r.provider_id].labels


async def test_a_new_preview_stops_an_unfinished_preview(gbox):
    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    gbox.add("x")
    worker, jobs = setup(gbox)
    old, p = await preview(gbox, worker, jobs)
    assert p.state == RuleJobState.PREVIEWED and p.planned == 1
    new = on_inbox(gbox, jobs)
    old_p = jobs.preview(gbox.acc.id, old)
    assert old_p.state == RuleJobState.STOPPED and old_p.planned == 0 and old_p.skipped == 1
    assert jobs.preview(gbox.acc.id, new).state == RuleJobState.PREVIEWING
    with gbox.sf() as db:
        reasons = set(db.scalars(select(RuleDecision.reason).where(RuleDecision.job_id == old)))
    assert reasons == {"The run was stopped."}


async def test_a_new_preview_stops_a_job_still_deciding(gbox):
    gbox.add("x")
    worker, jobs = setup(gbox)
    old = on_inbox(gbox, jobs)
    new = on_inbox(gbox, jobs)
    assert jobs.preview(gbox.acc.id, old).state == RuleJobState.STOPPED
    with gbox.sf() as db:
        todo_jobs = set(db.scalars(select(RuleTodo.job_id)))
    assert todo_jobs == {new}


async def test_second_preview_is_refused_while_a_job_is_running(gbox):
    from app.models import RuleJob

    gbox.add("x")
    worker, jobs = setup(gbox)
    jid = on_inbox(gbox, jobs)
    with gbox.sf() as db:
        db.get(RuleJob, jid).state = RuleJobState.RUNNING
        db.commit()
    with pytest.raises(RuleJobProblem, match="isn't finished"):
        on_inbox(gbox, jobs)


async def test_planned_change_shows_a_chip_only_while_its_job_is_open(gbox):
    from app.services.mail.listing import decision_chips

    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    m = gbox.add("x")
    worker, jobs = setup(gbox)
    jid, _ = await preview(gbox, worker, jobs)
    with gbox.sf() as db:
        assert decision_chips(db, [m.id])[m.id]["status"] == "planned"
        db.execute(update(RuleJob).where(RuleJob.id == jid).values(state=RuleJobState.STOPPED))
        db.commit()  # stopped without touching its decisions: the chip must still go
        assert decision_chips(db, [m.id]) == {}


async def test_mail_already_waiting_as_new_mail_is_left_out_of_the_job(gbox):
    from app.models import RuleTodo
    from app.services.rules.worker import set_sorting

    old, other = gbox.add("old"), gbox.add("other")
    with gbox.sf() as db:
        set_sorting(db, gbox.acc.id, True)
        db.add(RuleTodo(account_id=gbox.acc.id, message_id=old.id))
        db.commit()
    worker, jobs = setup(gbox)
    jid = on_inbox(gbox, jobs)
    assert jobs.preview(gbox.acc.id, jid).total == 1
    with gbox.sf() as db:
        assert db.get(RuleTodo, (gbox.acc.id, old.id)).job_id is None
        assert db.get(RuleTodo, (gbox.acc.id, other.id)).job_id == jid


async def test_worker_takes_new_mail_before_job_mail(gbox):
    from app.models import RuleJob, RuleTodo
    from app.services.rules.worker import set_sorting

    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    new, old = gbox.add("new"), gbox.add("old")
    worker, jobs = setup(gbox)
    worker.batch = 1
    jid = jobs.start_preview(
        gbox.acc.id, rule_ids=[news_rule(gbox)], scope=RunScope(message_ids=[old.id])
    )
    with gbox.sf() as db:
        set_sorting(db, gbox.acc.id, True)
        db.add(RuleTodo(account_id=gbox.acc.id, message_id=new.id, added_at=jobs.clock()))
        db.commit()
    await worker.run_once(gbox.acc.id)
    with gbox.sf() as db:
        assert db.get(RuleJob, jid).decided == 0
        assert db.scalar(select(RuleDecision).where(RuleDecision.message_id == new.id)) is not None


async def _ready(gbox, n=2):
    with gbox.sf() as db:
        rule = create_rule(db, gbox.acc.id, ARCHIVE, "a")
        rid = rule.id
    rows = [gbox.add(f"M{i}") for i in range(n)]
    worker, jobs = setup(gbox)
    jid, _ = await preview(gbox, worker, jobs)
    return rid, rows, jobs, jid


async def test_disabled_and_deleted_rules_are_skipped(gbox):
    from app.models import Rule

    rid, rows, jobs, jid = await _ready(gbox, 1)
    with gbox.sf() as db:
        db.get(Rule, rid).enabled = False
        db.commit()
    jobs.run(gbox.acc.id, jid, 1)
    await jobs.wait(jid)
    assert jobs.preview(gbox.acc.id, jid).skipped == 1


async def test_deleted_rule_is_skipped(gbox):
    from app.models import Rule

    rid, rows, jobs, jid = await _ready(gbox, 1)
    with gbox.sf() as db:
        db.delete(db.get(Rule, rid))
        db.commit()
    jobs.run(gbox.acc.id, jid, 1)
    await jobs.wait(jid)
    assert jobs.preview(gbox.acc.id, jid).skipped == 1
    assert "INBOX" in gbox.fake.messages[rows[0].provider_id].labels


async def test_unexpected_apply_error_counts_failed_and_run_continues(gbox, monkeypatch):
    rid, rows, jobs, jid = await _ready(gbox, 2)
    real, calls = gbox.applier.apply, []

    async def boom(did):
        calls.append(did)
        if len(calls) == 1:
            raise RuntimeError("bug")
        return await real(did)

    monkeypatch.setattr(gbox.applier, "apply", boom)
    jobs.run(gbox.acc.id, jid, 2)
    await jobs.wait(jid)
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.state, p.applied, p.failed) == (RuleJobState.DONE, 1, 1)
    with gbox.sf() as db:
        d = db.get(RuleDecision, calls[0])
        assert d.status == "failed" and d.reason == "Something went wrong applying this change."


async def test_stop_during_a_run_skips_the_rest(gbox, monkeypatch):
    import asyncio

    rid, rows, jobs, jid = await _ready(gbox, 2)
    real, gate = gbox.applier.apply, asyncio.Event()

    async def slow(did):
        gate.set()
        await asyncio.sleep(30)
        return await real(did)

    monkeypatch.setattr(gbox.applier, "apply", slow)
    jobs.run(gbox.acc.id, jid, 2)
    await gate.wait()
    jobs.stop(gbox.acc.id, jid)
    await jobs.wait(jid)
    p = jobs.preview(gbox.acc.id, jid)
    assert p.state == RuleJobState.STOPPED and p.skipped == 2 and p.planned == 0
    with gbox.sf() as db:
        reasons = set(db.scalars(select(RuleDecision.reason).where(RuleDecision.job_id == jid)))
    assert reasons == {"The run was stopped."}


async def test_resume_restarts_a_running_job(gbox):
    from app.models import RuleJob

    rid, rows, jobs, jid = await _ready(gbox, 2)
    with gbox.sf() as db:
        db.get(RuleJob, jid).state = RuleJobState.RUNNING
        db.commit()
    jobs.resume()
    jobs.resume()  # a second call must not start a second task
    await jobs.wait(jid)
    p = jobs.preview(gbox.acc.id, jid)
    assert p.state == RuleJobState.DONE and p.applied == 2


async def test_applying_a_planned_change_withdraws_older_suggestions(gbox):
    from app.models.enums import DecisionHow

    rid, rows, jobs, jid = await _ready(gbox, 1)
    with gbox.sf() as db:  # sorting had suggested something for the same mail earlier
        old = RuleDecision(
            account_id=gbox.acc.id,
            message_id=rows[0].id,
            rule_id=rid,
            rule_name="News",
            how=DecisionHow.AI,
            actions=[{"kind": "archive"}],
            labels_seen=["INBOX"],
            status="suggested",
        )
        db.add(old)
        db.commit()
        old_id = old.id
    jobs.run(gbox.acc.id, jid, 1)
    await jobs.wait(jid)
    assert jobs.preview(gbox.acc.id, jid).applied == 1
    with gbox.sf() as db:
        d = db.get(RuleDecision, old_id)
        assert d.status == "withdrawn" and d.reason == "A run of rules changed this mail."


# ---- a new run started while the worker is still deciding the old preview ---------------
def _job_state(gbox, jid):
    with gbox.sf() as db:
        j = db.get(RuleJob, jid)
        return j.decided, j.total, j.state


def _one_try_left(gbox, message_id):
    with gbox.sf() as db:
        db.get(RuleTodo, (gbox.acc.id, message_id)).attempts = 9  # the next failure gives up
        db.commit()


def _old_job_untouched(gbox, jid):
    """The stopped job counted nothing more and was not reopened after the stop."""
    assert _job_state(gbox, jid) == (0, 1, RuleJobState.STOPPED)


def _decisions_of(gbox, jid):
    with gbox.sf() as db:
        return list(db.scalars(select(RuleDecision).where(RuleDecision.job_id == jid)))


async def test_a_new_run_keeps_the_mail_the_worker_was_deciding_for_the_old_preview(gbox):
    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    m = gbox.add("M")
    entered, release = asyncio.Event(), asyncio.Event()

    class Held:
        async def choose(self, rules, mail, hints):
            if not release.is_set():
                entered.set()
                await release.wait()
            return Choice(rules[0].id, "fits")

    worker = SortingWorker(
        session_factory=gbox.sf, layout=gbox.layout, chooser=lambda: Held(), applier=gbox.applier
    )
    jobs = RuleJobs(gbox.sf, gbox.applier, worker)
    old = on_inbox(gbox, jobs)
    deciding = asyncio.ensure_future(worker.run_once(gbox.acc.id))
    await entered.wait()
    new = on_inbox(gbox, jobs)  # the old preview's dialog was closed
    release.set()
    await deciding
    assert _decisions_of(gbox, old) == []  # nothing is written for the stopped job
    while await worker.run_once(gbox.acc.id):
        pass
    assert _job_state(gbox, new) == (1, 1, RuleJobState.PREVIEWED)
    (d,) = _decisions_of(gbox, new)
    assert d.message_id == m.id and d.status == DecisionStatus.PLANNED
    assert jobs.preview(gbox.acc.id, new).planned == 1
    assert _decisions_of(gbox, old) == []
    _old_job_untouched(gbox, old)


async def test_a_give_up_for_the_old_preview_leaves_the_new_runs_mail_alone(gbox, monkeypatch):
    from app.services.rules import worker as worker_mod
    from app.services.rules.mailview import CopyUnreadable

    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    m = gbox.add("M")
    worker, jobs = setup(gbox)
    old = on_inbox(gbox, jobs)
    _one_try_left(gbox, m.id)
    real, started = worker_mod.mail_for_model, []

    def unreadable_once(*a, **kw):
        if not started:  # while the copy is being read, a new run takes the mail over
            started.append(on_inbox(gbox, jobs))
            _one_try_left(gbox, m.id)
            raise CopyUnreadable("gone")
        return real(*a, **kw)

    monkeypatch.setattr(worker_mod, "mail_for_model", unreadable_once)
    assert await worker.run_once(gbox.acc.id) == 0
    (new,) = started
    assert _decisions_of(gbox, old) == [] and _decisions_of(gbox, new) == []
    with gbox.sf() as db:
        todo = db.get(RuleTodo, (gbox.acc.id, m.id))
        assert todo.job_id == new and todo.attempts == 9 and todo.last_error is None
    assert _job_state(gbox, new) == (0, 1, RuleJobState.PREVIEWING)
    _old_job_untouched(gbox, old)
    while await worker.run_once(gbox.acc.id):
        pass
    assert _job_state(gbox, new) == (1, 1, RuleJobState.PREVIEWED)
    assert [d.status for d in _decisions_of(gbox, new)] == [DecisionStatus.PLANNED]
    assert _decisions_of(gbox, old) == []
    _old_job_untouched(gbox, old)


async def test_an_unreachable_model_for_the_old_preview_leaves_the_new_runs_mail_alone(gbox):
    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    m = gbox.add("M")
    started = []

    class DownAfterNewRun:
        async def choose(self, rules, mail, hints):
            started.append(on_inbox(gbox, jobs))
            raise AiUnreachable("down")

    worker = SortingWorker(
        session_factory=gbox.sf,
        layout=gbox.layout,
        chooser=lambda: DownAfterNewRun(),
        applier=gbox.applier,
    )
    jobs = RuleJobs(gbox.sf, gbox.applier, worker)
    old = on_inbox(gbox, jobs)
    await worker.run_once(gbox.acc.id)
    (new,) = started
    assert _decisions_of(gbox, old) == [] and _decisions_of(gbox, new) == []
    with gbox.sf() as db:
        todo = db.get(RuleTodo, (gbox.acc.id, m.id))
        assert todo.job_id == new and todo.attempts == 0 and todo.last_error is None
    assert _job_state(gbox, new) == (0, 1, RuleJobState.PREVIEWING)
    _old_job_untouched(gbox, old)


async def test_an_error_for_the_old_preview_does_not_give_up_the_new_runs_mail(gbox):
    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    m = gbox.add("M")
    started = []

    class BoomAfterNewRun:
        async def choose(self, rules, mail, hints):
            started.append(on_inbox(gbox, jobs))
            _one_try_left(gbox, m.id)
            raise RuntimeError("boom")

    worker = SortingWorker(
        session_factory=gbox.sf,
        layout=gbox.layout,
        chooser=lambda: BoomAfterNewRun(),
        applier=gbox.applier,
    )
    jobs = RuleJobs(gbox.sf, gbox.applier, worker)
    old = on_inbox(gbox, jobs)
    _one_try_left(gbox, m.id)
    await worker.run_once(gbox.acc.id)
    (new,) = started
    assert _decisions_of(gbox, old) == [] and _decisions_of(gbox, new) == []
    with gbox.sf() as db:
        todo = db.get(RuleTodo, (gbox.acc.id, m.id))
        assert todo.job_id == new and todo.attempts == 9 and todo.last_error is None
    assert _job_state(gbox, new) == (0, 1, RuleJobState.PREVIEWING)
    _old_job_untouched(gbox, old)


async def test_a_new_run_started_between_the_check_and_the_write_keeps_its_mail(gbox):
    """The new run lands after the worker made sure the mail was still its job's, but before
    it wrote anything (here: just before its first flush). The worker's claim on the to-do row
    must be its first write, so the new run waits for it instead of slipping in between."""
    from sqlalchemy import event

    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    m = gbox.add("M")
    worker, jobs = setup(gbox)
    old = on_inbox(gbox, jobs)
    seen = {}

    def start_new_run():
        seen["new"] = on_inbox(gbox, jobs)
        seen["old_decided"] = _job_state(gbox, old)[0]

    def before_first_flush(session, flush_context, instances):
        if "thread" not in seen:
            seen["thread"] = threading.Thread(target=start_new_run)
            seen["thread"].start()
            seen["thread"].join(0.5)  # it gets in now, or waits for the worker's write

    event.listen(gbox.sf, "before_flush", before_first_flush)
    try:
        await worker.run_once(gbox.acc.id)
        seen["thread"].join()
    finally:
        event.remove(gbox.sf, "before_flush", before_first_flush)
    new = seen["new"]
    while await worker.run_once(gbox.acc.id):
        pass
    assert _job_state(gbox, new) == (1, 1, RuleJobState.PREVIEWED)
    (d,) = _decisions_of(gbox, new)
    assert d.message_id == m.id and d.status == DecisionStatus.PLANNED
    decided, _total, state = _job_state(gbox, old)
    assert state == RuleJobState.STOPPED and decided == seen["old_decided"]
    assert all(x.status == DecisionStatus.SKIPPED for x in _decisions_of(gbox, old))


async def test_new_mail_is_not_sorted_when_sorting_is_turned_off_meanwhile(gbox):
    from app.services.rules.worker import enqueue_new, set_sorting

    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    m = gbox.add("M")
    with gbox.sf() as db:
        set_sorting(db, gbox.acc.id, True)
        enqueue_new(db, gbox.acc.id, m)
        db.commit()

    class TurnedOffMeanwhile:
        async def choose(self, rules, mail, hints):
            with gbox.sf() as db:
                set_sorting(db, gbox.acc.id, False)
                db.commit()
            return Choice(rules[0].id, "fits")

    worker = SortingWorker(
        session_factory=gbox.sf,
        layout=gbox.layout,
        chooser=lambda: TurnedOffMeanwhile(),
        applier=gbox.applier,
    )
    assert await worker.run_once(gbox.acc.id) == 0
    with gbox.sf() as db:
        assert list(db.scalars(select(RuleDecision))) == []
        assert list(db.scalars(select(RuleTodo))) == []
