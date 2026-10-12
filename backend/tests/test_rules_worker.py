from datetime import timedelta

from sqlalchemy import select

from app.models import RuleAccount, RuleDecision, RuleTodo
from app.models.enums import DecisionStatus, RuleMode
from app.services.ai.client import AiUnreachable
from app.services.mail.store import eml_path
from app.services.rules.match import Choice
from app.services.rules.store import RuleIn, create_rule
from app.services.rules.worker import SortingWorker, enqueue_new, retry_after, set_sorting


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


async def test_no_rule_is_logged_and_mail_not_in_gmail_is_dropped(gbox):
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
