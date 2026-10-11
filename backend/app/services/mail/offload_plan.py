"""Mail offload: what one would do (the preview, spec 5.2), and tidying after a stop."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import as_utc, utcnow
from app.models import Account, MailMessage, MailOffload
from app.models.enums import MailOffloadState, MailState
from app.paths import Layout
from app.services.mail.search import SearchError, mail_conditions
from app.services.mail.store import eml_path

GMAIL_TRASH_DAYS = 30  # Gmail empties its trash after this many days (spec 5.4)
INTERRUPTED_OFFLOAD_MSG = (
    "CleanSync stopped during this offload. Mails already offloaded stay offloaded; "
    "the rest stayed in Gmail."
)


def recover_mail_offloads(db: Session) -> int:
    """At startup: an offload a stop cut short ends as FAILED, with the reason.

    Only RUNNING ones. A WAITING offload (not started yet, or paused by the budget until
    the next UTC day) keeps waiting.
    """
    rows = list(
        db.scalars(select(MailOffload).where(MailOffload.state == MailOffloadState.RUNNING))
    )
    for off in rows:
        off.state = MailOffloadState.FAILED
        off.reason = INTERRUPTED_OFFLOAD_MSG
        off.finished_at = utcnow()
    db.commit()
    return len(rows)


TOO_NEW = "too_new"
NOT_BACKED_UP = "not_backed_up"
ALREADY = "already"
NO_FILTER_MSG = "Choose at least one filter, or pick mails in the list."
_EXCLUDED = {
    TOO_NEW: "{n} left out: newer than {days} days. Recent mail always stays in Gmail.",
    NOT_BACKED_UP: "{n} left out: not fully backed up here.",
    ALREADY: "{n} left out: already offloaded or deleted in Gmail.",
}


def _mails(n: int) -> str:
    return f"{n:,} {'mail' if n == 1 else 'mails'}"


def excluded_text(reason: str, n: int, days: int) -> str:
    return _EXCLUDED[reason].format(n=_mails(n), days=days)


@dataclass
class Pick:
    q: str | None = None
    message_ids: list[int] | None = None
    thread_ids: list[str] | None = None

    def describe(self, n: int) -> str:
        if self.message_ids or self.thread_ids:
            return f"{n:,} picked {'mail' if n == 1 else 'mails'}"
        return (self.q or "").strip()


@dataclass
class MailBrief:
    id: int
    subject: str
    from_addr: str
    date: datetime
    size: int


@dataclass
class OffloadPreview:
    filter_text: str
    count: int
    bytes: int
    largest: list[MailBrief] = field(default_factory=list)
    oldest: list[MailBrief] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    message_ids: list[int] = field(default_factory=list)


def candidates(db: Session, account_id: int, pick: Pick, now: datetime) -> list[MailMessage]:
    base = select(MailMessage).where(MailMessage.account_id == account_id)
    if pick.message_ids:
        return list(db.scalars(base.where(MailMessage.id.in_(pick.message_ids))))
    if pick.thread_ids:
        return list(db.scalars(base.where(MailMessage.thread_id.in_(pick.thread_ids))))
    q = (pick.q or "").strip()
    if not q:
        raise SearchError(NO_FILTER_MSG)
    return list(
        db.scalars(select(MailMessage).where(*mail_conditions(db, account_id, q=q, now=now)))
    )


def file_ok(layout: Layout, email: str, row: MailMessage) -> bool:
    """The file is there with the size it had when it was saved (the full check, with the
    SHA-256 and "reads as a mail", is made right before the mail goes to the trash)."""
    try:
        return eml_path(layout, email, row.file_path).stat().st_size == row.file_size
    except OSError:
        return False


def _brief(r: MailMessage) -> MailBrief:
    return MailBrief(r.id, r.subject, r.from_addr, r.date, r.size)


def plan_offload(
    db: Session, layout: Layout, account: Account, pick: Pick, *, min_age_days: int, now: datetime
) -> OffloadPreview:
    rows = candidates(db, account.id, pick, now)
    cutoff = now - timedelta(days=min_age_days)
    kept: list[MailMessage] = []
    why: Counter[str] = Counter()
    for r in rows:
        if r.state != MailState.IN_MAIL:
            why[ALREADY] += 1
        elif as_utc(r.date) > cutoff:
            why[TOO_NEW] += 1  # spec 5.2: never offloaded
        elif not file_ok(layout, account.email, r):
            why[NOT_BACKED_UP] += 1
        else:
            kept.append(r)
    excluded = [
        {"reason": k, "count": why[k], "text": excluded_text(k, why[k], min_age_days)}
        for k in (TOO_NEW, NOT_BACKED_UP, ALREADY)
        if why[k]
    ]
    by_date = sorted(kept, key=lambda r: (as_utc(r.date), r.id))
    return OffloadPreview(
        filter_text=pick.describe(len(rows)),
        count=len(kept),
        bytes=sum(r.size for r in kept),
        largest=[_brief(r) for r in sorted(kept, key=lambda r: (-r.size, r.id))[:10]],
        oldest=[_brief(r) for r in by_date[:10]],
        excluded=excluded,
        message_ids=[r.id for r in by_date],
    )
