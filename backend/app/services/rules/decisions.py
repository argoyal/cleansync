"""What the owner does with decisions (AI rules spec §6): approve, reject, correct, undo, and
what the rules learn from it: remembered senders (3 approvals) and correction hints."""

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import MailMessage, Rule, RuleCorrection, RuleDecision, RuleSender
from app.models.enums import DecisionHow, DecisionStatus
from app.services.rules.apply import WAIT_ERRORS, Applier, UndoResult
from app.services.rules.mailview import sender_of
from app.services.rules.store import get_rule

REMEMBER_AFTER = 3


class DecisionProblem(ValueError):
    pass


def remembered_senders(db: Session, account_id: int) -> dict[str, int]:
    rows = db.execute(
        select(RuleSender.sender, RuleSender.rule_id)
        .join(Rule, Rule.id == RuleSender.rule_id)
        .where(
            RuleSender.account_id == account_id,
            RuleSender.approvals >= REMEMBER_AFTER,
            Rule.enabled.is_(True),
        )
        .order_by(Rule.position)
    ).all()
    out: dict[str, int] = {}
    for sender, rule_id in rows:
        out.setdefault(sender, rule_id)  # the rule higher in the list wins a tie
    return out


def hints_for(db: Session, account_id: int, sender: str, limit: int = 3) -> list[str]:
    if not sender:
        return []
    rows = db.scalars(
        select(RuleCorrection)
        .where(RuleCorrection.account_id == account_id, RuleCorrection.sender == sender)
        .order_by(RuleCorrection.id.desc())
        .limit(limit)
    )
    return [
        f'"{c.subject[:80]}" was not {c.from_rule or "no rule"}; it belongs to '
        f"{c.to_rule or 'no rule'}."
        for c in rows
    ]


def learn(db: Session, account_id: int, rule_id: int, sender: str, now) -> None:
    if not sender:
        return
    row = db.scalar(
        select(RuleSender).where(RuleSender.rule_id == rule_id, RuleSender.sender == sender)
    )
    if row is None:
        row = RuleSender(rule_id=rule_id, account_id=account_id, sender=sender, approvals=0)
        db.add(row)
    row.approvals += 1
    if row.approvals >= REMEMBER_AFTER and row.learned_at is None:
        row.learned_at = now


def forget(db: Session, account_id: int, rule_id: int, sender: str) -> None:
    for row in db.scalars(
        select(RuleSender).where(
            RuleSender.account_id == account_id,
            RuleSender.rule_id == rule_id,
            RuleSender.sender == sender,
        )
    ):
        db.delete(row)


class Decisions:
    def __init__(self, session_factory, applier: Applier, clock=utcnow) -> None:
        self.sf = session_factory
        self.applier = applier
        self.clock = clock

    def _mine(self, db: Session, account_id: int, ids: list[int]) -> list[RuleDecision]:
        if not ids:
            return []
        return list(
            db.scalars(
                select(RuleDecision).where(
                    RuleDecision.account_id == account_id, RuleDecision.id.in_(ids)
                )
            )
        )

    async def approve(self, account_id: int, ids: list[int]) -> dict[str, int]:
        with self.sf() as db:
            todo = [
                (d.id, d.rule_id, d.message_id)
                for d in self._mine(db, account_id, ids)
                if d.status == DecisionStatus.SUGGESTED
            ]
        counts: Counter = Counter()
        for did, rule_id, message_id in todo:
            try:
                status = await self.applier.apply(did)
            except WAIT_ERRORS:
                counts["waiting"] += 1
                continue
            counts[status] += 1
            if status == DecisionStatus.APPLIED and rule_id is not None:
                with self.sf() as db:
                    row = db.get(MailMessage, message_id)
                    learn(db, account_id, rule_id, sender_of(row.from_addr), self.clock())
                    db.commit()
        return dict(counts)

    def reject(self, account_id: int, ids: list[int]) -> int:
        with self.sf() as db:
            n = 0
            for d in self._mine(db, account_id, ids):
                if d.status in (DecisionStatus.SUGGESTED, DecisionStatus.PLANNED):
                    d.status, d.reason = DecisionStatus.REJECTED, "Rejected by you."
                    n += 1
            db.commit()
            return n

    async def correct(
        self, account_id: int, decision_id: int, to_rule_id: int | None
    ) -> int | None:
        with self.sf() as db:
            mine = self._mine(db, account_id, [decision_id])
            if not mine:
                raise DecisionProblem("That decision isn't here any more.")
            d = mine[0]
            message_id = d.message_id
            row = db.get(MailMessage, message_id)
            sender, subject = sender_of(row.from_addr), row.subject or ""
            from_rule_id, from_name, status = d.rule_id, d.rule_name, d.status
            to_rule = get_rule(db, account_id, to_rule_id) if to_rule_id is not None else None
            to_name = to_rule.name if to_rule else ""
            to_fields = (
                (to_rule.id, to_rule.version, to_rule.name, list(to_rule.actions))
                if to_rule
                else None
            )
        if status == DecisionStatus.APPLIED:
            await self.applier.undo(decision_id)
        elif status in (DecisionStatus.SUGGESTED, DecisionStatus.PLANNED):
            with self.sf() as db:
                x = db.get(RuleDecision, decision_id)
                x.status, x.reason = DecisionStatus.REJECTED, "Corrected by you."
                db.commit()
        new_id = None
        with self.sf() as db:
            if from_rule_id is not None:
                forget(db, account_id, from_rule_id, sender)
            db.add(
                RuleCorrection(
                    account_id=account_id,
                    sender=sender,
                    subject=subject,
                    from_rule=from_name or "",
                    to_rule=to_name,
                )
            )
            if to_fields is not None:
                rid, version, name, actions = to_fields
                row = db.get(MailMessage, message_id)
                nd = RuleDecision(
                    account_id=account_id,
                    message_id=message_id,
                    rule_id=rid,
                    rule_version=version,
                    rule_name=name,
                    how=DecisionHow.MANUAL,
                    why="Corrected by you.",
                    actions=actions,
                    labels_seen=list(row.labels or []),
                    status=DecisionStatus.SUGGESTED,
                )
                db.add(nd)
                db.flush()
                new_id = nd.id
                learn(db, account_id, rid, sender, self.clock())
            db.commit()
        if new_id is not None:
            await self.applier.apply(new_id)
        return new_id

    async def undo(self, account_id: int, ids: list[int]) -> list[UndoResult]:
        with self.sf() as db:
            todo = [d.id for d in self._mine(db, account_id, ids)]
        return [await self.applier.undo(did) for did in todo]
