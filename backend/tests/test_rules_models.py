from datetime import UTC, datetime

from sqlalchemy import select

from app.models import (
    AiConnection,
    AiTask,
    MailMessage,
    Rule,
    RuleAccount,
    RuleDecision,
    RuleTodo,
    RuleVersion,
)
from app.models.enums import (
    AiTaskName,
    DecisionHow,
    DecisionStatus,
    MailState,
    RuleCombine,
    RuleMode,
)

WHEN = datetime(2026, 10, 1, tzinfo=UTC)


def _mail(db, acc, pid="m1"):
    row = MailMessage(
        account_id=acc.id,
        provider_id=pid,
        thread_id=pid,
        date=WHEN,
        size=10,
        file_size=10,
        sha256="x" * 64,
        file_path=f"2026/10/{pid}.eml",
    )
    db.add(row)
    db.commit()
    return row


def test_rule_tables_round_trip(db, make_account):
    acc = make_account()
    rule = Rule(
        account_id=acc.id,
        name="Newsletters",
        description="Newsletters I never read",
        conditions={"list_unsubscribe": True},
        combine=RuleCombine.FIXED_THEN_AI,
        actions=[{"kind": "archive"}],
        mode=RuleMode.SUGGEST,
        position=0,
    )
    db.add(rule)
    db.commit()
    db.add(
        RuleVersion(rule_id=rule.id, version=1, snapshot={"name": "Newsletters"}, saved_by="admin")
    )
    mail = _mail(db, acc)
    db.add(RuleTodo(account_id=acc.id, message_id=mail.id, next_try_at=WHEN))
    db.add(
        RuleDecision(
            account_id=acc.id,
            message_id=mail.id,
            rule_id=rule.id,
            rule_version=1,
            how=DecisionHow.AI,
            why="a digest",
            actions=[{"kind": "archive"}],
            changes={"add": [], "remove": ["INBOX"], "trash": False},
            labels_seen=["INBOX"],
            status=DecisionStatus.SUGGESTED,
        )
    )
    db.add(RuleAccount(account_id=acc.id, sorting_enabled=True))
    conn = AiConnection(name="Ollama", base_url="http://ollama:11434/v1")
    db.add(conn)
    db.commit()
    db.add(AiTask(task=AiTaskName.SORTING, connection_id=conn.id, model="llama3.1:8b"))
    db.commit()
    assert db.scalar(select(RuleDecision)).status == "suggested"
    assert rule.enabled is True and rule.version == 1
    assert MailState.LOCAL_ARCHIVE == "local_archive"


def test_deleting_a_mail_removes_its_decisions_and_todo(db, make_account):
    acc = make_account()
    mail = _mail(db, acc)
    db.add(RuleTodo(account_id=acc.id, message_id=mail.id, next_try_at=WHEN))
    db.add(
        RuleDecision(
            account_id=acc.id,
            message_id=mail.id,
            how=DecisionHow.NONE,
            status=DecisionStatus.NO_RULE,
        )
    )
    db.commit()
    db.delete(mail)
    db.commit()
    assert db.scalars(select(RuleDecision)).all() == []
    assert db.scalars(select(RuleTodo)).all() == []


def test_deleting_a_rule_keeps_decisions_without_it(db, make_account):
    acc = make_account()
    rule = Rule(account_id=acc.id, name="R", description="d", position=0)
    db.add(rule)
    mail = _mail(db, acc)
    db.commit()
    db.add(
        RuleDecision(
            account_id=acc.id,
            message_id=mail.id,
            rule_id=rule.id,
            how=DecisionHow.FIXED,
            status=DecisionStatus.APPLIED,
        )
    )
    db.commit()
    db.delete(rule)
    db.commit()
    db.expire_all()
    assert db.scalar(select(RuleDecision)).rule_id is None
