"""Running rules (running rules spec §3-§5, §8): a run decides with its own rules only, covers
the Inbox, a search, a pick-all or a pick, and is approved from its match list."""

import re

import pytest
from sqlalchemy import select

from app.models import MailMessage, RuleDecision, RuleJob, RuleSender, RuleTodo
from app.models.enums import DecisionStatus, MailState, RuleJobState
from app.services.rules.decisions import remembered_senders
from app.services.rules.jobs import CHANGED_MSG, RuleJobProblem, RuleJobs, RunScope
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
        ([news], RunScope(view="all", q='""'), "Search first, or pick some mail."),
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


async def decided(gbox, worker, jobs, rule_ids, scope) -> int:
    """Start a run and decide all of it."""
    jid = jobs.start_preview(gbox.acc.id, rule_ids=rule_ids, scope=scope)
    while await worker.run_once(gbox.acc.id):
        pass
    return jid


async def test_the_match_list_pages_and_filters_by_rule(gbox):
    news, shops = rule(gbox, "News"), rule(gbox, "Shops")
    for i in range(3):
        gbox.add(f"News {i}")
    gbox.add("Shops one")
    gbox.add("Nothing here")
    worker, jobs = jobs_for(gbox)
    scope = RunScope(view="inbox", q="", pick_all=True)
    jid = await decided(gbox, worker, jobs, [news, shops], scope)
    first = jobs.matches(gbox.acc.id, jid, limit=3)
    rest = jobs.matches(gbox.acc.id, jid, before_id=first[-1].id, limit=3)
    ids = [m.id for m in first + rest]
    assert (len(first), len(rest)) == (3, 1) and ids == sorted(ids, reverse=True)
    assert {m.subject for m in first + rest} == {"News 0", "News 1", "News 2", "Shops one"}
    (only,) = jobs.matches(gbox.acc.id, jid, rule_id=shops)
    assert (only.subject, only.rule_name, only.why, only.actions) == (
        "Shops one",
        "Shops",
        "subject mentions Shops",
        ARCHIVE,
    )
    assert only.from_addr == "friend@example.com" and only.thread_id
    with pytest.raises(RuleJobProblem):
        jobs.matches(gbox.acc.id + 1, jid)


async def test_approving_leaves_out_the_unticked_and_runs_the_rest(gbox):
    news = rule(gbox, "News")
    kept, changed, moved = gbox.add("News kept"), gbox.add("News changed"), gbox.add("News moved")
    worker, jobs = jobs_for(gbox)
    jid = await decided(gbox, worker, jobs, [news], RunScope(inbox=True))
    ids = {m.subject: m.id for m in jobs.matches(gbox.acc.id, jid)}
    with gbox.sf() as db:
        db.get(MailMessage, moved.id).labels = ["INBOX", "STARRED"]  # changed since deciding
        db.commit()
    jobs.run(gbox.acc.id, jid, left_out=[ids["News kept"], 999_999])  # a stranger id is ignored
    await jobs.wait(jid)
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.state, p.applied, p.skipped, p.left_out) == (RuleJobState.DONE, 1, 1, 1)
    assert p.skip_reasons == [{"reason": CHANGED_MSG, "count": 1}]
    assert "INBOX" in gbox.fake.messages[kept.provider_id].labels
    assert "INBOX" not in gbox.fake.messages[changed.provider_id].labels
    with gbox.sf() as db:
        d = db.get(RuleDecision, ids["News kept"])
        assert (d.status, d.reason) == (DecisionStatus.REJECTED, "You left it out.")


async def test_only_runs_just_the_ticked_matches(gbox):
    news = rule(gbox, "News")
    rows = [gbox.add(f"News {i}") for i in range(3)]
    worker, jobs = jobs_for(gbox)
    jid = await decided(gbox, worker, jobs, [news], RunScope(inbox=True))
    ids = {m.subject: m.id for m in jobs.matches(gbox.acc.id, jid)}
    jobs.run(gbox.acc.id, jid, only=[ids["News 1"]])
    await jobs.wait(jid)
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.applied, p.left_out) == (1, 2)
    labels = [gbox.fake.messages[r.provider_id].labels for r in rows]
    assert ["INBOX" in x for x in labels] == [True, False, True]


async def test_the_typed_count_is_asked_only_for_picked_offloads(gbox, monkeypatch):
    news = rule(gbox, "News")
    junk = rule(gbox, "Junk", actions=[{"kind": "offload"}])
    for subject in ("News a", "Junk a", "Junk b"):
        gbox.add(subject)
    applied: list[int] = []

    async def apply(did):
        applied.append(did)
        with gbox.sf() as db:
            db.get(RuleDecision, did).status = DecisionStatus.APPLIED
            db.commit()
        return DecisionStatus.APPLIED

    monkeypatch.setattr(gbox.applier, "apply", apply)
    worker, jobs = jobs_for(gbox)
    scope = RunScope(view="inbox", q="", pick_all=True)
    jid = await decided(gbox, worker, jobs, [news, junk], scope)
    ids = {m.subject: m.id for m in jobs.matches(gbox.acc.id, jid)}
    with pytest.raises(
        RuleJobProblem, match=r"^The number you typed doesn't match the 2 mails to offload\.$"
    ):
        jobs.run(gbox.acc.id, jid)
    with pytest.raises(RuleJobProblem, match=r"doesn't match the 1 mail to offload\.$"):
        jobs.run(gbox.acc.id, jid, 2, left_out=[ids["Junk b"]])
    jobs.run(gbox.acc.id, jid, 1, left_out=[ids["Junk b"]])
    await jobs.wait(jid)
    assert sorted(applied) == sorted([ids["News a"], ids["Junk a"]])


def test_the_sample_mailbox_has_mail_the_marketing_starter_fits():
    import email
    from datetime import UTC, datetime

    from app.devtools import fake_model
    from app.devtools.fake_gmail import SAMPLE_TOTAL, FakeGmail, sample_mailbox
    from app.services.rules.store import STARTERS

    fake = FakeGmail("alice@example.com")
    sample_mailbox(fake, datetime(2026, 10, 13, tzinfo=UTC))
    assert len(fake.messages) == SAMPLE_TOTAL == 28
    heads = [email.message_from_bytes(m.raw) for m in fake.messages.values()]
    lists = sorted(str(h["Subject"]) for h in heads if h["List-Unsubscribe"])
    assert lists == ["Big sale: 30% off shoes", "Spring offers just for you"]
    marketing = STARTERS["Marketing"].description
    for subject in lists:  # the pretend model picks Marketing for both, as the browser test needs
        asked = f"1. Marketing: {marketing}\n<<<EMAIL\nSubject: {subject}\n"
        assert fake_model._choose(asked)["rule"] == 1


async def test_the_per_rule_counts_are_planned_mails_one_group_per_rule_in_the_rules_order(gbox):
    news, shops = rule(gbox, "News"), rule(gbox, "Shops")
    for i in range(3):
        gbox.add(f"Shops {i}")
    gbox.add("News one")
    gbox.add("News two")
    worker, jobs = jobs_for(gbox)
    jid = await decided(
        gbox, worker, jobs, [shops, news], RunScope(inbox=False, view="inbox", pick_all=True)
    )
    with gbox.sf() as db:
        rows = list(
            db.scalars(
                select(RuleDecision).where(RuleDecision.job_id == jid).order_by(RuleDecision.id)
            )
        )
        by_rule = {
            n: [d for d in rows if d.rule_id == r] for n, r in (("shops", shops), ("news", news))
        }
        by_rule["shops"][0].status = DecisionStatus.REJECTED  # left behind by a start that failed
        by_rule["news"][0].rule_name = "Old name"  # the rule was renamed while it ran
        db.commit()
    per = jobs.preview(gbox.acc.id, jid).per_rule
    # News comes first in the rules' order although Shops has more mails; Shops counts only
    # the planned ones; the renamed rule is one group under its current name.
    assert [(g["rule"], g["count"]) for g in per] == [("News", 2), ("Shops", 2)]


async def test_a_pick_all_on_a_quoted_empty_search_is_described_without_the_quotes(gbox):
    news = rule(gbox, "News")
    gbox.add("News one")
    worker, jobs = jobs_for(gbox)
    jid = await decided(gbox, worker, jobs, [news], RunScope(view="inbox", q='""', pick_all=True))
    assert '""' not in jobs.preview(gbox.acc.id, jid).filter_text


async def test_approving_a_run_a_newer_run_replaced_says_so(gbox):
    news = rule(gbox, "News")
    gbox.add("News one")
    worker, jobs = jobs_for(gbox)
    old = await decided(gbox, worker, jobs, [news], RunScope(inbox=True))
    await decided(gbox, worker, jobs, [news], RunScope(inbox=True))  # replaces the first
    with pytest.raises(
        RuleJobProblem,
        match=r"^A newer run replaced this one\. Close this and start again\.$",
    ):
        jobs.run(gbox.acc.id, old)


async def test_a_run_still_previewing_says_to_wait(gbox):
    news = rule(gbox, "News")
    gbox.add("News one")
    _, jobs = jobs_for(gbox)
    jid = jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=RunScope(inbox=True))
    with pytest.raises(RuleJobProblem, match=r"^Wait until the preview is finished\.$"):
        jobs.run(gbox.acc.id, jid)


async def test_a_planned_change_whose_rule_was_deleted_is_not_counted_and_not_run(gbox):
    news = rule(gbox, "News")
    gone = gbox.add("News gone")
    gbox.add("News stays")
    worker, jobs = jobs_for(gbox)
    jid = await decided(gbox, worker, jobs, [news], RunScope(inbox=True))
    with gbox.sf() as db:  # the rule disappeared from one decision, still PLANNED
        d = db.scalar(select(RuleDecision).where(RuleDecision.message_id == gone.id))
        d.rule_id = None
        db.commit()
        did = d.id
    p = jobs.preview(gbox.acc.id, jid)
    assert p.planned == 1
    assert p.skip_reasons == [{"reason": "Its rule was deleted.", "count": 1}]
    assert [m.subject for m in jobs.matches(gbox.acc.id, jid)] == ["News stays"]
    jobs.run(gbox.acc.id, jid)
    await jobs.wait(jid)
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.state, p.applied, p.skipped) == (RuleJobState.DONE, 1, 1)
    assert "INBOX" in gbox.fake.messages[gone.provider_id].labels
    with gbox.sf() as db:
        d = db.get(RuleDecision, did)
        assert (d.status, d.reason) == (DecisionStatus.SKIPPED, "Its rule was deleted.")


async def test_an_empty_pick_at_approve_is_refused_and_starts_nothing(gbox):
    news = rule(gbox, "News")
    gbox.add("News one")
    worker, jobs = jobs_for(gbox)
    jid = await decided(gbox, worker, jobs, [news], RunScope(inbox=True))
    with pytest.raises(
        RuleJobProblem,
        match=r"^Pick at least one mail, or close this to leave them all\.$",
    ):
        jobs.run(gbox.acc.id, jid, only=[])
    assert jobs.preview(gbox.acc.id, jid).state == RuleJobState.PREVIEWED
    assert jobs.preview(gbox.acc.id, jid).left_out == 0


async def test_two_kinds_of_pick_at_once_are_refused(gbox):
    news = rule(gbox, "News")
    m = gbox.add("News one")
    _, jobs = jobs_for(gbox)
    scopes = [
        RunScope(inbox=True, message_ids=[m.id]),
        RunScope(message_ids=[m.id], thread_ids=[m.thread_id]),
        RunScope(thread_ids=[m.thread_id], q="news"),
        RunScope(inbox=True, pick_all=True),
        RunScope(message_ids=[m.id], q="news"),
    ]
    for scope in scopes:
        with pytest.raises(RuleJobProblem, match=r"^Pick one thing to run on"):
            jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=scope)
    with gbox.sf() as db:
        assert db.scalars(select(RuleJob)).all() == []


def test_the_start_endpoint_answers_400_for_two_kinds_of_pick(auth_client, db, make_account):
    acc = make_account()
    r = auth_client.post(
        "/api/rule-jobs",
        json={"account_id": acc.id, "rule_ids": [1], "inbox": True, "message_ids": [1]},
    )
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Pick one thing to run on")


async def test_a_run_whose_row_is_missing_decides_with_no_rules(gbox):
    rule(gbox, "News")
    m = gbox.add("News one")
    with gbox.sf() as db:
        db.add(RuleTodo(account_id=gbox.acc.id, message_id=m.id, job_id=None))
        db.commit()
    from app.services.rules.worker import job_rules

    with gbox.sf() as db:
        assert job_rules(db, 9999) == []  # no such run: no rules, not every rule
        assert job_rules(db, None) is None


async def test_a_sorting_todo_next_to_a_run_with_rule_ids_still_uses_every_rule(gbox):
    shops, news = rule(gbox, "Shops"), rule(gbox, "News")
    in_run = gbox.add("Shops and News run")
    plain = gbox.add("Shops and News plain")
    chooser = ByName()
    jid = job_with(gbox, [news], in_run)
    with gbox.sf() as db:  # new-mail sorting: a to-do row with no job
        from app.models import RuleAccount

        db.add(RuleAccount(account_id=gbox.acc.id, sorting_enabled=True))
        db.add(RuleTodo(account_id=gbox.acc.id, message_id=plain.id, job_id=None))
        db.commit()
    await decide_all(gbox, chooser)
    assert sorted(chooser.offered) == [["News"], ["Shops", "News"]]
    with gbox.sf() as db:
        by_subject = dict(
            db.execute(
                select(MailMessage.subject, RuleDecision.rule_id).join(
                    MailMessage, MailMessage.id == RuleDecision.message_id
                )
            ).all()
        )
    assert by_subject == {"Shops and News run": news, "Shops and News plain": shops}
    assert jid


async def test_a_rule_turned_off_or_deleted_mid_run_decides_no_rule(gbox):
    from app.services.rules.store import delete_rule, update_rule

    off, gone = rule(gbox, "Off"), rule(gbox, "Gone")
    a, b = gbox.add("Off mail"), gbox.add("Gone mail")
    jid = job_with(gbox, [off, gone], a, b)
    with gbox.sf() as db:
        update_rule(
            db,
            gbox.acc.id,
            off,
            RuleIn(name="Off", description="Off mail", actions=ARCHIVE, enabled=False),
            "a",
        )
        delete_rule(db, gbox.acc.id, gone)
    chooser = ByName()
    await decide_all(gbox, chooser)
    assert {n for n, _ in verdicts(gbox, jid).values()} == {""}
    with gbox.sf() as db:
        statuses = set(db.scalars(select(RuleDecision.status).where(RuleDecision.job_id == jid)))
    assert statuses == {DecisionStatus.NO_RULE}
    assert all(o == [] for o in chooser.offered)


async def test_other_runs_and_other_mailboxes_ids_in_left_out_and_only_are_ignored(
    gbox, make_account
):
    news = rule(gbox, "News")
    for i in range(2):
        gbox.add(f"News {i}")
    worker, jobs = jobs_for(gbox)
    other_run = await decided(gbox, worker, jobs, [news], RunScope(inbox=True))
    foreign = {m.id for m in jobs.matches(gbox.acc.id, other_run)}
    mine = await decided(gbox, worker, jobs, [news], RunScope(inbox=True))  # replaces the first
    own = {m.id for m in jobs.matches(gbox.acc.id, mine)}
    assert own and not own & foreign
    bob = make_account("bob@example.com")
    with gbox.sf() as db:  # a decision of another mailbox
        from app.models import MailMessage as MM

        bm = MM(
            account_id=bob.id,
            provider_id="b1",
            thread_id="b1",
            from_addr="",
            to_addrs="",
            cc_addrs="",
            subject="x",
            date=db.scalar(select(MailMessage.date).limit(1)),
            size=1,
            file_size=1,
            sha256="0" * 64,
            labels=[],
            attachments=[],
            snippet="",
            file_path="x",
            state="in_mail",
        )
        db.add(bm)
        db.flush()
        bd = RuleDecision(
            account_id=bob.id,
            message_id=bm.id,
            status=DecisionStatus.PLANNED,
            actions=ARCHIVE,
            rule_name="x",
        )
        db.add(bd)
        db.commit()
        bob_decision = bd.id
    jobs.run(gbox.acc.id, mine, left_out=[*foreign, bob_decision])
    await jobs.wait(mine)
    p = jobs.preview(gbox.acc.id, mine)
    assert (p.applied, p.left_out) == (2, 0)  # none of the strangers' ids left anything out
    with gbox.sf() as db:
        assert db.get(RuleDecision, bob_decision).status == DecisionStatus.PLANNED
        assert {
            d.status for d in db.scalars(select(RuleDecision).where(RuleDecision.id.in_(foreign)))
        } != {DecisionStatus.REJECTED}


async def test_a_picked_thread_leaves_out_its_offloaded_mail(gbox):
    news = rule(gbox, "News")
    first = gbox.add("News")
    old = gbox.add("Re: News")
    with gbox.sf() as db:
        row = db.get(MailMessage, old.id)
        row.thread_id, row.state = first.thread_id, MailState.OFFLOADED
        db.commit()
    _, jobs = jobs_for(gbox)
    scope = RunScope(view="all", thread_ids=[first.thread_id])
    jid = jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=scope)
    assert covered(gbox, jid) == [first.id]
