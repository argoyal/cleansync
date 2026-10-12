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
from app.models import MailLabel, MailMessage, MailMessageLabel
from app.models.enums import MailState
from app.services.mail.gmail import valid_mail_id
from app.services.mail.search import SearchError

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
    nxt = encode_cursor(groups[-1][1], groups[-1][0]) if more and groups else None
    return rows, nxt


def totals(db: Session, conds: list) -> tuple[int, int]:
    count, size = db.execute(
        select(func.count(), func.coalesce(func.sum(MailMessage.size), 0)).where(*conds)
    ).one()
    return int(count), int(size)


def _label_tally(db: Session, account_id: int) -> dict[str, int]:
    rows = db.execute(
        select(MailMessageLabel.label_id, func.count())
        .where(MailMessageLabel.account_id == account_id)
        .group_by(MailMessageLabel.label_id)
    ).all()
    return {str(v): int(n) for v, n in rows}


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
    return {
        "all": sum(states.values()),
        "inbox": tally.get("INBOX", 0),
        "sent": tally.get("SENT", 0),
        "starred": tally.get("STARRED", 0),
        "important": tally.get("IMPORTANT", 0),
        "offloaded": states.get(MailState.OFFLOADED, 0),
        "deleted": states.get(MailState.DELETED, 0),
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
