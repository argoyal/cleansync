"""The message list (spec §6.1): threads, newest first, a page at a time; and the numbers
the Mail panel shows."""

import base64
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.clock import as_utc
from app.models import MailLabel, MailMessage, MailMessageLabel, RuleDecision, RuleJob
from app.models.enums import DecisionStatus, MailState, RuleJobState
from app.services.mail.gmail import valid_mail_id
from app.services.mail.search import SearchError, archived_conditions

BAD_CURSOR_MSG = "That page of the list is no longer valid. Start from the top."


@dataclass
class ThreadRow:
    thread_id: str
    count: int
    last_date: datetime
    message_id: int  # the newest mail of the thread that matches
    from_addr: str
    subject: str
    snippet: str
    has_attachments: bool
    unread: bool
    offloaded: bool
    deleted: bool
    decision: dict | None = None


CHIP_STATUSES = (DecisionStatus.SUGGESTED, DecisionStatus.APPLIED)
# A planned change is shown only while its run of rules can still make it.
OPEN_JOB_STATES = (RuleJobState.PREVIEWING, RuleJobState.PREVIEWED, RuleJobState.RUNNING)


def decision_chips(db: Session, message_ids: list[int]) -> dict[int, dict]:
    """The newest shown decision of each mail: a suggestion, a planned change or an applied one."""
    if not message_ids:
        return {}
    out: dict[int, dict] = {}
    for d in db.scalars(
        select(RuleDecision)
        .outerjoin(RuleJob, RuleJob.id == RuleDecision.job_id)
        .where(
            RuleDecision.message_id.in_(message_ids),
            or_(
                RuleDecision.status.in_(CHIP_STATUSES),
                and_(
                    RuleDecision.status == DecisionStatus.PLANNED,
                    RuleJob.state.in_(OPEN_JOB_STATES),
                ),
            ),
        )
        .order_by(RuleDecision.id)
    ):
        out[d.message_id] = {
            "id": d.id,
            "rule_name": d.rule_name or "Offloaded by you",
            "status": d.status,
            "how": d.how,
            "why": d.why,
        }
    return out


def encode_cursor(date: datetime, thread_id: str) -> str:
    raw = json.dumps([as_utc(date).isoformat(), thread_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        when, thread_id = json.loads(raw)
        if valid_mail_id(thread_id) is None:
            raise ValueError("not a thread id")
        return datetime.fromisoformat(when), thread_id
    except (ValueError, TypeError):
        raise SearchError(BAD_CURSOR_MSG) from None


WALK_CHUNK = 500  # mails read per statement while looking for a page of threads


def walk_statement(conds: list, key: tuple | None):
    """One chunk of matching mails, newest first, strictly below `key` (date, thread_id, id)."""
    q = select(MailMessage.thread_id, MailMessage.date, MailMessage.id).where(*conds)
    if key is not None:
        d, t, i = key
        q = q.where(
            or_(
                MailMessage.date < d,
                and_(MailMessage.date == d, MailMessage.thread_id < t),
                and_(MailMessage.date == d, MailMessage.thread_id == t, MailMessage.id < i),
            )
        )
    return q.order_by(
        MailMessage.date.desc(), MailMessage.thread_id.desc(), MailMessage.id.desc()
    ).limit(WALK_CHUNK)


def _newest_first(db: Session, conds: list, start: tuple | None):
    """Chunks of (thread_id, date, id) of matching mails, newest first. Each chunk is its own
    short statement, so no read lock is held between them (the database is not in WAL mode)."""
    key = start
    while True:
        rows = db.execute(walk_statement(conds, key)).all()
        yield rows
        if len(rows) < WALK_CHUNK:
            return
        key = (rows[-1].date, rows[-1].thread_id, rows[-1].id)


def _shown_before(db: Session, conds: list, tids: list[str], when: datetime, tid: str) -> set:
    """Of these threads, the ones with a matching mail at or above the cursor: an earlier
    page showed them already."""
    if not tids:
        return set()
    return set(
        db.scalars(
            select(MailMessage.thread_id)
            .where(
                *conds,
                MailMessage.thread_id.in_(tids),
                or_(
                    MailMessage.date > when,
                    and_(MailMessage.date == when, MailMessage.thread_id >= tid),
                ),
            )
            .distinct()
        )
    )


def list_threads(
    db: Session, conds: list, *, cursor: str | None = None, limit: int = 50
) -> tuple[list[ThreadRow], str | None]:
    """Keyset paging on (newest date, thread id), so paging on never shows a thread twice
    or skips one that was there. A thread that gets new mail after you've paged past it
    moves to the top, and shows up there after a refresh.

    Walks mails newest first and stops once it has a page, instead of grouping the whole
    mailbox (issue #16): the first time a thread turns up is its newest matching mail."""
    when = tid = None
    start = None
    if cursor:
        when, tid = decode_cursor(cursor)
        # Every mail strictly below (when, tid); no mail has id -1, so the id part of the key
        # never lets a mail of (when, tid) itself through.
        start = (when, tid, -1)
    order: list[tuple[str, datetime]] = []
    seen: set[str] = set()
    for rows in _newest_first(db, conds, start):
        fresh = []
        for r in rows:
            if r.thread_id not in seen:
                seen.add(r.thread_id)
                fresh.append((r.thread_id, r.date))
        if cursor:
            old = _shown_before(db, conds, [t for t, _ in fresh], when, tid)
            fresh = [f for f in fresh if f[0] not in old]
        order.extend(fresh)
        if len(order) > limit:
            break
    more = len(order) > limit
    groups = order[:limit]
    tids = [t for t, _ in groups]
    by_thread: dict[str, list[MailMessage]] = defaultdict(list)
    if tids:
        for m in db.scalars(
            select(MailMessage)
            .where(*conds, MailMessage.thread_id.in_(tids))
            .order_by(MailMessage.date.desc(), MailMessage.id.desc())
        ):
            by_thread[m.thread_id].append(m)
    rows = []
    for t, last in groups:
        msgs = by_thread[t]
        newest = msgs[0]
        rows.append(
            ThreadRow(
                thread_id=t,
                count=len(msgs),
                last_date=last,
                message_id=newest.id,
                from_addr=newest.from_addr,
                subject=newest.subject,
                snippet=newest.snippet,
                has_attachments=any(m.has_attachments for m in msgs),
                unread=any(m.unread for m in msgs),
                offloaded=any(m.state == MailState.OFFLOADED for m in msgs),
                deleted=any(m.state == MailState.DELETED for m in msgs),
            )
        )
    chips = decision_chips(db, [r.message_id for r in rows])
    for r in rows:
        r.decision = chips.get(r.message_id)
    nxt = encode_cursor(groups[-1][1], groups[-1][0]) if more and groups else None
    return rows, nxt


def totals(db: Session, conds: list) -> tuple[int, int]:
    count, size = db.execute(
        select(func.count(), func.coalesce(func.sum(MailMessage.size), 0)).where(*conds)
    ).one()
    return int(count), int(size)


def _label_tally(db: Session, account_id: int) -> dict[str, int]:
    """Mails per label, counting only mail in Gmail (mail panel spec §4). The label table
    counts every mail kept here, so the ones out of Gmail are taken off again."""
    rows = db.execute(
        select(MailMessageLabel.label_id, func.count())
        .where(MailMessageLabel.account_id == account_id)
        .group_by(MailMessageLabel.label_id)
    ).all()
    tally = {str(v): int(n) for v, n in rows}
    gone = db.execute(
        select(MailMessageLabel.label_id, func.count())
        .join(MailMessage, MailMessage.id == MailMessageLabel.message_id)
        .where(
            MailMessage.account_id == account_id,
            MailMessage.state != MailState.IN_MAIL,
        )
        .group_by(MailMessageLabel.label_id)
    ).all()
    for v, n in gone:
        left = tally.get(str(v), 0) - int(n)
        if left > 0:
            tally[str(v)] = left
        else:
            tally.pop(str(v), None)
    return tally


def mail_counts(db: Session, account_id: int) -> tuple[dict[str, int], list[dict]]:
    """The view counts and the label counts, reading the label tally once."""
    tally = _label_tally(db, account_id)
    return view_counts(db, account_id, tally), label_counts(db, account_id, tally)


def view_counts(
    db: Session, account_id: int, tally: dict[str, int] | None = None
) -> dict[str, int]:
    tally = _label_tally(db, account_id) if tally is None else tally
    states = dict(
        db.execute(
            select(MailMessage.state, func.count())
            .where(MailMessage.account_id == account_id)
            .group_by(MailMessage.state)
        ).all()
    )
    archived = (
        db.scalar(
            select(func.count())
            .select_from(MailMessage)
            .where(
                MailMessage.account_id == account_id,
                MailMessage.state == MailState.IN_MAIL,
                *archived_conditions(account_id),
            )
        )
        or 0
    )
    suggested = (
        db.scalar(
            select(func.count(func.distinct(RuleDecision.message_id)))
            .join(MailMessage, MailMessage.id == RuleDecision.message_id)
            .where(
                RuleDecision.account_id == account_id,
                RuleDecision.status == DecisionStatus.SUGGESTED,
                MailMessage.state == MailState.IN_MAIL,
            )
        )
        or 0
    )
    return {
        "all": states.get(MailState.IN_MAIL, 0),
        "inbox": tally.get("INBOX", 0),
        "sent": tally.get("SENT", 0),
        "starred": tally.get("STARRED", 0),
        "important": tally.get("IMPORTANT", 0),
        "archived": archived,
        "offloaded": states.get(MailState.OFFLOADED, 0),
        "deleted": states.get(MailState.DELETED, 0),
        "suggested": suggested,
    }


def label_counts(db: Session, account_id: int, tally: dict[str, int] | None = None) -> list[dict]:
    tally = _label_tally(db, account_id) if tally is None else tally
    labels = db.scalars(
        select(MailLabel).where(MailLabel.account_id == account_id, MailLabel.type == "user")
    )
    out = [
        {
            "id": lab.provider_label_id,
            "name": lab.name,
            "count": tally.get(lab.provider_label_id, 0),
        }
        for lab in labels
    ]
    return sorted(out, key=lambda x: x["name"].lower())
