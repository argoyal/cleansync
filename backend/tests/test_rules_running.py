"""Running rules (running rules spec §3-§5, §8): a run decides with its own rules only, covers
the Inbox, a search, a pick-all or a pick, and is approved from its match list."""

import re

import pytest
from sqlalchemy import select

from app.models import MailMessage, RuleDecision, RuleJob, RuleSender, RuleTodo
from app.services.rules.decisions import remembered_senders
from app.services.rules.jobs import RuleJobProblem, RuleJobs, RunScope
from app.services.rules.match import Choice
from app.services.rules.store import RuleIn, create_rule
from app.services.rules.worker import SortingWorker

ARCHIVE = [{"kind": "archive"}]


class ByName:
    """A pretend model: the first rule it is offered whose name is in the subject. It keeps
    the names it was offered, one list per mail."""

    def __init__(self):
        self.offered: list[list[str]] = []

    async def choose(self, rules, mail, hints):
        self.offered.append([r.name for r in rules])
        for r in rules:
            if r.name.lower() in mail.subject.lower():
                return Choice(r.id, f"subject mentions {r.name}")
        return Choice(None, "nothing fits")


def rule(gbox, name, actions=ARCHIVE, **kw) -> int:
    with gbox.sf() as db:
        data = RuleIn(name=name, description=f"{name} mail", actions=actions, **kw)
        return create_rule(db, gbox.acc.id, data, "a").id


def worker_for(gbox, chooser) -> SortingWorker:
    return SortingWorker(
        session_factory=gbox.sf, layout=gbox.layout, chooser=lambda: chooser, applier=gbox.applier
    )


async def decide_all(gbox, chooser) -> None:
    w = worker_for(gbox, chooser)
    while await w.run_once(gbox.acc.id):
        pass


def job_with(gbox, rule_ids, *rows) -> int:
    """A run as it is queued. A run from before migration 0012 has rule_ids []."""
    with gbox.sf() as db:
        job = RuleJob(account_id=gbox.acc.id, total=len(rows), rule_ids=rule_ids)
        db.add(job)
        db.flush()
        for r in rows:
            db.add(RuleTodo(account_id=gbox.acc.id, message_id=r.id, job_id=job.id))
        db.commit()
        return job.id


def verdicts(gbox, jid) -> dict[str, tuple[str, str]]:
    """Subject -> (the rule's name, how it was decided), for one run."""
    with gbox.sf() as db:
        rows = db.execute(
            select(MailMessage.subject, RuleDecision.rule_name, RuleDecision.how)
            .join(MailMessage, MailMessage.id == RuleDecision.message_id)
            .where(RuleDecision.job_id == jid)
        ).all()
    return {s: (n, str(h)) for s, n, h in rows}


def remember(gbox, rule_id, sender) -> None:
    with gbox.sf() as db:
        db.add(RuleSender(rule_id=rule_id, account_id=gbox.acc.id, sender=sender, approvals=3))
        db.commit()


async def test_a_run_decides_with_only_its_rules(gbox):
    rule(gbox, "Shops")
    news = rule(gbox, "News")
    both = gbox.add("Shops and News")
    shops_only = gbox.add("Shops sale")
    news_only = gbox.add("News today")
    chooser = ByName()
    jid = job_with(gbox, [news], both, shops_only, news_only)
    await decide_all(gbox, chooser)
    assert verdicts(gbox, jid) == {
        "Shops and News": ("News", "ai"),  # Shops comes first, but isn't part of this run
        "Shops sale": ("", "ai"),  # only Shops fits: no rule
        "News today": ("News", "ai"),
    }
    assert chooser.offered == [["News"], ["News"], ["News"]]


async def test_a_run_from_before_the_upgrade_uses_every_enabled_rule(gbox):
    rule(gbox, "Shops")
    rule(gbox, "News")
    rule(gbox, "Off", enabled=False)
    m = gbox.add("Shops and News and Off")
    chooser = ByName()
    jid = job_with(gbox, [], m)
    await decide_all(gbox, chooser)
    assert verdicts(gbox, jid) == {"Shops and News and Off": ("Shops", "ai")}
    assert chooser.offered == [["Shops", "News"]]


async def test_remembered_senders_count_only_for_the_runs_rules(gbox):
    shops, news = rule(gbox, "Shops"), rule(gbox, "News")
    remember(gbox, shops, "a@example.com")
    remember(gbox, shops, "both@example.com")
    remember(gbox, news, "both@example.com")  # Shops, higher up, wins this tie in sorting
    from_a = gbox.add("News from a", sender="a@example.com")
    from_both = gbox.add("Hello", sender="both@example.com")
    chooser = ByName()
    jid = job_with(gbox, [news], from_a, from_both)
    await decide_all(gbox, chooser)
    assert verdicts(gbox, jid) == {
        "News from a": ("News", "ai"),
        "Hello": ("News", "remembered"),
    }
    assert chooser.offered == [["News"]]
    with gbox.sf() as db:
        assert remembered_senders(db, gbox.acc.id) == {
            "a@example.com": shops,
            "both@example.com": shops,
        }
        assert remembered_senders(db, gbox.acc.id, rule_ids=[news]) == {"both@example.com": news}


def jobs_for(gbox, chooser=None) -> tuple[SortingWorker, RuleJobs]:
    w = worker_for(gbox, chooser or ByName())
    return w, RuleJobs(gbox.sf, gbox.applier, w)


def covered(gbox, jid) -> list[int]:
    """The mails a run queued for deciding."""
    with gbox.sf() as db:
        return sorted(db.scalars(select(RuleTodo.message_id).where(RuleTodo.job_id == jid)))


async def test_the_inbox_run_covers_only_inbox_mail(gbox):
    news = rule(gbox, "News")
    _, jobs = jobs_for(gbox)
    with pytest.raises(RuleJobProblem, match=r"^No mail in the Inbox to run this rule on\.$"):
        jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=RunScope(inbox=True))
    inbox = gbox.add("In the inbox")
    gbox.add("Archived", labels=("Label_1",))
    gbox.add("Sent", labels=("SENT",))
    jid = jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=RunScope(inbox=True))
    assert covered(gbox, jid) == [inbox.id]
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.rule_ids, p.view, p.q, p.filter_text) == ([news], "inbox", "", "Inbox")


async def test_a_search_or_a_pick_all_runs_in_its_view_and_label(gbox):
    news = rule(gbox, "News")
    a = gbox.add("News one")
    b = gbox.add("News two", labels=("Label_1",))
    other = gbox.add("Other")
    _, jobs = jobs_for(gbox)

    def run_on(**scope) -> list[int]:
        jid = jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=RunScope(**scope))
        return covered(gbox, jid)

    assert run_on(view="all", q="news") == sorted([a.id, b.id])
    assert run_on(view="inbox", q="news") == [a.id]
    assert run_on(view="archived", q="news") == [b.id]
    assert run_on(view="all", label="Label_1", q="", pick_all=True) == [b.id]
    assert run_on(view="inbox", q="", pick_all=True) == sorted([a.id, other.id])
    jid = jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=RunScope(view="inbox", q=" news "))
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.view, p.q, p.filter_text) == ("inbox", "news", "Inbox · news")


async def test_picked_threads_cover_only_their_mails_in_the_view(gbox):
    news = rule(gbox, "News")
    first = gbox.add("News")
    reply = gbox.add("Re: News", labels=("SENT",))
    with gbox.sf() as db:
        db.get(MailMessage, reply.id).thread_id = first.thread_id
        db.commit()
    _, jobs = jobs_for(gbox)

    def run_on(view) -> list[int]:
        scope = RunScope(view=view, thread_ids=[first.thread_id])
        return covered(gbox, jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=scope))

    assert run_on("inbox") == [first.id]  # the owner's reply in Sent isn't part of it
    assert run_on("all") == sorted([first.id, reply.id])
    jid = jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=RunScope(message_ids=[reply.id]))
    assert covered(gbox, jid) == [reply.id]
    assert jobs.preview(gbox.acc.id, jid).filter_text == "1 picked mail"


async def test_a_start_needs_rules_and_a_search_or_a_pick(gbox, make_account):
    news, other = rule(gbox, "News"), rule(gbox, "Other")
    off = rule(gbox, "Off", enabled=False)
    bob = make_account("bob@example.com")
    with gbox.sf() as db:
        bobs = create_rule(db, bob.id, RuleIn(name="Bob's", description="x", actions=ARCHIVE), "a")
        bobs_id = bobs.id
    gbox.add("News")
    _, jobs = jobs_for(gbox)
    cases = [
        ([], RunScope(inbox=True), "Pick at least one rule."),
        ([news, other], RunScope(inbox=True), "Run on the Inbox takes one rule."),
        ([news], RunScope(view="inbox"), "Search first, or pick some mail."),
        ([news], RunScope(view="all", q="   "), "Search first, or pick some mail."),
        ([news], RunScope(view="offloaded", q="news"), "Rules run on mail in Gmail's views only."),
        (
            [news],
            RunScope(view="suggested", pick_all=True),
            "Rules run on mail in Gmail's views only.",
        ),
        ([off], RunScope(inbox=True), "Turn the rule on to run it."),
        ([news + 1000], RunScope(inbox=True), "That rule no longer exists."),
        ([bobs_id], RunScope(inbox=True), "That rule no longer exists."),
        ([news], RunScope(view="all", q="in:spam"), '"in:" only knows in:archived.'),
    ]
    for rule_ids, scope, msg in cases:
        with pytest.raises(RuleJobProblem, match=f"^{re.escape(msg)}$"):
            jobs.start_preview(gbox.acc.id, rule_ids=rule_ids, scope=scope)
    with gbox.sf() as db:
        assert db.scalars(select(RuleJob)).all() == []


async def test_each_mail_gets_the_first_ticked_rule_that_fits_in_the_rules_order(gbox):
    rule(gbox, "Alpha")
    beta, gamma = rule(gbox, "Beta"), rule(gbox, "Gamma")
    gbox.add("Gamma Beta Alpha")
    chooser = ByName()
    worker, jobs = jobs_for(gbox, chooser)
    scope = RunScope(view="all", q="gamma")
    jid = jobs.start_preview(gbox.acc.id, rule_ids=[gamma, beta], scope=scope)  # ticked in reverse
    while await worker.run_once(gbox.acc.id):
        pass
    assert verdicts(gbox, jid) == {"Gamma Beta Alpha": ("Beta", "ai")}
    assert chooser.offered == [["Beta", "Gamma"]]
    assert jobs.preview(gbox.acc.id, jid).rule_ids == [gamma, beta]
