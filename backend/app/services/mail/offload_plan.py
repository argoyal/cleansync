"""Mail offload: what one would do (the preview, spec 5.2), and tidying after a stop."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.core.clock import as_utc, utcnow
from app.models import Account, MailJob, MailLabel, MailMessage, MailOffload
from app.models.enums import JobState, MailJobKind, MailOffloadKind, MailOffloadState, MailState
from app.paths import Layout
from app.services.mail.search import SearchError, mail_conditions, parse_query
from app.services.mail.store import eml_path

GMAIL_TRASH_DAYS = 30  # Gmail empties its trash after this many days (spec 5.4)
RESTARTED_OFFLOAD_MSG = "CleanSync restarted during this offload. It goes on by itself."
STOPPED_MSG = "Stopped by you. What wasn't offloaded yet stayed in Gmail."
# Delete forever jobs have their own words (they never "offload").
FOREVER_REST = "What wasn't deleted yet stays in Gmail, with CleanSync's copy."
FOREVER_RESTARTED_MSG = "CleanSync restarted during this Delete forever. It goes on by itself."
FOREVER_STOPPED_MSG = f"Stopped by you. {FOREVER_REST}"
# Stopped while the last batch's outcome was not known (Gmail didn't answer, or CleanSync
# restarted during the call): those mails may already be out of Gmail.
UNKNOWN_REST = (
    "The last batch may already be out of Gmail. Those mails will show in Deleted in Gmail, "
    "and their copies are kept."
)
STOPPED_UNKNOWN_MSG = f"Stopped by you. {UNKNOWN_REST} The rest stayed in Gmail."
FOREVER_STOPPED_UNKNOWN_MSG = f"Stopped by you. {UNKNOWN_REST} The rest stays in Gmail."
STOPPED_MESSAGES = (
    STOPPED_MSG,
    FOREVER_STOPPED_MSG,
    STOPPED_UNKNOWN_MSG,
    FOREVER_STOPPED_UNKNOWN_MSG,
)


def recover_mail_offloads(db: Session) -> int:
    """At startup: an offload a restart cut short waits again, and the minute tick starts it.
    A batch Gmail did but CleanSync never recorded is then sent again (bulk offload spec
    §4.4). Its mail job ends INTERRUPTED with the same words, so Activity and the offload's
    page agree (runs before recover_mail_jobs, which would say something else). One the owner
    asked to stop ends stopped. A WAITING offload keeps waiting."""
    rows = list(
        db.scalars(select(MailOffload).where(MailOffload.state == MailOffloadState.RUNNING))
    )
    now = utcnow()
    for off in rows:
        forever = off.kind == MailOffloadKind.DELETE_FOREVER
        if off.stop_asked:
            off.state, off.finished_at = MailOffloadState.FAILED, now
            # A restart can cut a batch's Gmail call short: its outcome is not known.
            if forever:
                off.reason = FOREVER_STOPPED_UNKNOWN_MSG
            else:
                off.reason = STOPPED_UNKNOWN_MSG
        else:
            off.state = MailOffloadState.WAITING
            off.reason = FOREVER_RESTARTED_MSG if forever else RESTARTED_OFFLOAD_MSG
        # One mail job runs at a time per account: its running offload job is this one's.
        for job in db.scalars(
            select(MailJob).where(
                MailJob.account_id == off.account_id,
                MailJob.kind.in_([MailJobKind.OFFLOAD, MailJobKind.DELETE_FOREVER]),
                MailJob.state == JobState.RUNNING,
            )
        ):
            job.state, job.message, job.completed_at = JobState.INTERRUPTED, off.reason, now
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
    """What an action works on: picked mails or threads, or a pick-all as the view, the
    label and the search (mail panel spec §6)."""

    q: str | None = None
    message_ids: list[int] | None = None
    thread_ids: list[str] | None = None
    view: str = "all"
    label: str | None = None


VIEW_NAMES = {
    "inbox": "Inbox",
    "sent": "Sent",
    "starred": "Starred",
    "important": "Important",
    "archived": "Archived",
    "suggested": "Suggested",
    "offloaded": "Offloaded",
    "deleted": "Deleted in Gmail",
}


def describe_pick(db: Session, account_id: int, pick: Pick, n: int) -> str:
    """The offload's name on its page: "12 picked mails", or the view, label and search."""
    if pick.message_ids or pick.thread_ids:
        return f"{n:,} picked {'mail' if n == 1 else 'mails'}"
    parts = [VIEW_NAMES[pick.view]] if pick.view in VIEW_NAMES else []
    if pick.label:
        name = db.scalar(
            select(MailLabel.name).where(
                MailLabel.account_id == account_id, MailLabel.provider_label_id == pick.label
            )
        )
        parts.append(name or pick.label)
    if (pick.q or "").strip():
        parts.append((pick.q or "").strip())
    return " · ".join(parts)


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


IN_CHUNK = 900  # SQLite allows few bound values in one query; stay far below the limit
BOTH_PICKS_MSG = "Pick either mails or threads, not both."
_NEEDED = (
    MailMessage.id,
    MailMessage.account_id,
    MailMessage.thread_id,
    MailMessage.subject,
    MailMessage.from_addr,
    MailMessage.date,
    MailMessage.size,
    MailMessage.file_size,
    MailMessage.file_path,
    MailMessage.state,
)


def chunked(items: list, n: int = IN_CHUNK):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def rows_where_in(db: Session, account_id: int, column, values: list) -> list[MailMessage]:
    """The account's mails whose `column` is one of `values`, asked for in small chunks."""
    out: list[MailMessage] = []
    for part in chunked(list(values)):
        out.extend(
            db.scalars(
                select(MailMessage)
                .where(MailMessage.account_id == account_id, column.in_(part))
                .options(load_only(*_NEEDED))
            )
        )
    return out


def candidates(db: Session, account_id: int, pick: Pick, now: datetime) -> list[MailMessage]:
    if pick.message_ids and pick.thread_ids:
        raise SearchError(BOTH_PICKS_MSG)
    if pick.message_ids:
        return rows_where_in(db, account_id, MailMessage.id, pick.message_ids)
    if pick.thread_ids:
        return rows_where_in(db, account_id, MailMessage.thread_id, pick.thread_ids)
    q = (pick.q or "").strip()
    # "" (an empty phrase) is an empty search too, so ask the parser, not the raw text
    if parse_query(q, now=now).is_empty() and pick.view == "all" and not pick.label:
        raise SearchError(NO_FILTER_MSG)  # one tick never offloads a whole mailbox
    stmt = (
        select(MailMessage)
        .where(*mail_conditions(db, account_id, view=pick.view, label=pick.label, q=q, now=now))
        .options(load_only(*_NEEDED))
    )
    return list(db.scalars(stmt))


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
        filter_text=describe_pick(db, account.id, pick, len(rows)),
        count=len(kept),
        bytes=sum(r.size for r in kept),
        largest=[_brief(r) for r in sorted(kept, key=lambda r: (-r.size, r.id))[:10]],
        oldest=[_brief(r) for r in by_date[:10]],
        excluded=excluded,
        message_ids=[r.id for r in by_date],
    )
