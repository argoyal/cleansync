"""What the owner's own moves in Gmail tell the rules (AI rules spec §6): a mail CleanSync
archived that comes back to the inbox is "moved back by you" and is never sorted again; a
mail the owner moved before a suggestion was approved has its suggestion withdrawn.
Imports only models, so mail/index.py can call it."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import as_utc, utcnow
from app.models import MailMessage, RuleDecision
from app.models.enums import DecisionStatus

MOVED_YOURSELF = "You moved this mail yourself before it was approved."


def note_label_change(
    db: Session,
    row: MailMessage,
    before: set[str],
    after: set[str],
    now: datetime,
    seen_at: datetime | None = None,
) -> None:
    """`seen_at` is when Gmail's list of changes was fetched: a decision CleanSync applied
    after that moment is not something the owner did, so it is not read as one."""
    seen = as_utc(seen_at)
    decisions = list(db.scalars(select(RuleDecision).where(RuleDecision.message_id == row.id)))
    if "INBOX" in after and "INBOX" not in before:
        for d in decisions:
            if (
                d.status == DecisionStatus.APPLIED
                and d.moved_back_at is None
                and "INBOX" in ((d.changes or {}).get("remove") or [])
                and (seen is None or (d.applied_at is not None and as_utc(d.applied_at) <= seen))
            ):
                d.moved_back_at = now
    if ("INBOX" in before and "INBOX" not in after) or ("TRASH" in after - before):
        if seen is not None and any(
            d.applied_at is not None and as_utc(d.applied_at) > seen for d in decisions
        ):
            return
        for d in decisions:
            if d.status == DecisionStatus.SUGGESTED:
                d.status, d.reason = DecisionStatus.WITHDRAWN, MOVED_YOURSELF


def note_restored(db: Session, row: MailMessage, now: datetime | None = None) -> None:
    """The owner restored this mail from Offloaded: a rule that offloaded it is not to
    offload it again, so its decision counts as "moved back by you" (not committed)."""
    for d in db.scalars(select(RuleDecision).where(RuleDecision.message_id == row.id)):
        if (
            d.status == DecisionStatus.APPLIED
            and d.moved_back_at is None
            and (d.changes or {}).get("trash")
        ):
            d.moved_back_at = now or utcnow()


def moved_back(db: Session, message_id: int) -> bool:
    return (
        db.scalar(
            select(RuleDecision.id).where(
                RuleDecision.message_id == message_id, RuleDecision.moved_back_at.is_not(None)
            )
        )
        is not None
    )
