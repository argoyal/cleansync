"""Running rules (running rules spec §3-§5, §8): a run decides with its own rules only, covers
the Inbox, a search, a pick-all or a pick, and is approved from its match list."""

from sqlalchemy import select

from app.models import MailMessage, RuleDecision, RuleJob, RuleSender, RuleTodo
from app.services.rules.decisions import remembered_senders
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
