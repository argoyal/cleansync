import pytest
from sqlalchemy import select

from app.models import MailMessage, RuleDecision
from app.models.enums import RuleJobState
from app.services.rules.jobs import RuleJobProblem, RuleJobs
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


async def preview(gbox, worker, jobs, **kw):
    jid = jobs.start_preview(gbox.acc.id, **kw)
    while await worker.run_once(gbox.acc.id):
        pass
    return jid, jobs.preview(gbox.acc.id, jid)


async def test_preview_then_run_applies_with_the_typed_count(gbox):
    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    rows = [gbox.add(f"D{i}") for i in range(3)]
    worker, jobs = setup(gbox)
    jid, p = await preview(gbox, worker, jobs, q="", view="inbox")
    assert p.state == RuleJobState.PREVIEWED and p.planned == 3
    assert p.per_rule[0]["rule"] == "News" and p.per_rule[0]["count"] == 3
    assert all("INBOX" in gbox.fake.messages[r.provider_id].labels for r in rows)  # nothing yet
    with pytest.raises(RuleJobProblem, match="doesn't match"):
        jobs.run(gbox.acc.id, jid, 2)
    jobs.run(gbox.acc.id, jid, 3)
    await jobs.wait(jid)
    assert jobs.preview(gbox.acc.id, jid).applied == 3
    assert all("INBOX" not in gbox.fake.messages[r.provider_id].labels for r in rows)


async def test_mail_changed_since_preview_is_skipped_and_listed(gbox):
    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    a = gbox.add("A")
    gbox.add("B")
    worker, jobs = setup(gbox)
    jid, _ = await preview(gbox, worker, jobs, view="inbox")
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
    jid, _ = await preview(gbox, worker, jobs, view="inbox")
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
        jobs.start_preview(gbox.acc.id, view="inbox")
    gbox.add("x")
    monkeypatch.setattr("app.services.rules.jobs.MAX_JOB", 0)
    with pytest.raises(RuleJobProblem, match="narrow"):
        jobs.start_preview(gbox.acc.id, view="inbox")


async def test_stop_drops_remaining_work(gbox):
    with gbox.sf() as db:
        create_rule(db, gbox.acc.id, ARCHIVE, "a")
    gbox.add("x")
    worker, jobs = setup(gbox)
    jid = jobs.start_preview(gbox.acc.id, view="inbox")
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
    jid, _ = await preview(gbox, worker, jobs, view="inbox")
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
