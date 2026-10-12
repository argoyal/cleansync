"""What the owner's own moves in Gmail tell the rules (AI rules spec §6): a mail CleanSync
archived that comes back to the inbox is "moved back by you" and is never sorted again; a
mail the owner moved before a suggestion was approved has its suggestion withdrawn.
Imports only models, so mail/index.py can call it."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MailMessage, RuleDecision
from app.models.enums import DecisionStatus

MOVED_YOURSELF = "You moved this mail yourself before it was approved."


def note_label_change(
    db: Session, row: MailMessage, before: set[str], after: set[str], now: datetime
) -> None:
    if "INBOX" in after and "INBOX" not in before:
        d = db.scalar(
            select(RuleDecision)
            .where(
                RuleDecision.message_id == row.id,
                RuleDecision.status == DecisionStatus.APPLIED,
                RuleDecision.moved_back_at.is_(None),
            )
            .order_by(RuleDecision.id.desc())
        )
        if d is not None and "INBOX" in ((d.changes or {}).get("remove") or []):
            d.moved_back_at = now
    if ("INBOX" in before and "INBOX" not in after) or ("TRASH" in after - before):
        for d in db.scalars(
            select(RuleDecision).where(
                RuleDecision.message_id == row.id,
                RuleDecision.status == DecisionStatus.SUGGESTED,
            )
        ):
            d.status, d.reason = DecisionStatus.WITHDRAWN, MOVED_YOURSELF


def moved_back(db: Session, message_id: int) -> bool:
    return (
        db.scalar(
            select(RuleDecision.id).where(
                RuleDecision.message_id == message_id, RuleDecision.moved_back_at.is_not(None)
            )
        )
        is not None
    )
