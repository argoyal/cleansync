"""The message list (spec §6.1): threads, newest first, a page at a time; and the numbers
the Mail panel shows."""

import base64
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.clock import as_utc
from app.models import MailLabel, MailMessage, RuleDecision
from app.models.enums import DecisionStatus, MailState
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
    decision: dict | None = None


CHIP_STATUSES = (DecisionStatus.SUGGESTED, DecisionStatus.APPLIED, DecisionStatus.PLANNED)


def decision_chips(db: Session, message_ids: list[int]) -> dict[int, dict]:
    """The newest shown decision of each mail: a suggestion, a planned change or an applied one."""
    if not message_ids:
        return {}
    out: dict[int, dict] = {}
    for d in db.scalars(
        select(RuleDecision)
        .where(RuleDecision.message_id.in_(message_ids), RuleDecision.status.in_(CHIP_STATUSES))
        .order_by(RuleDecision.id)
    ):
        out[d.message_id] = {
            "id": d.id,
            "rule_name": d.rule_name or "Deleted by you",
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


def list_threads(
    db: Session, conds: list, *, cursor: str | None = None, limit: int = 50
) -> tuple[list[ThreadRow], str | None]:
    """Keyset paging on (newest date, thread id), so paging on never shows a thread twice
    or skips one that was there. A thread that gets new mail after you've paged past it
    moves to the top, and shows up there after a refresh."""
    last = func.max(MailMessage.date)
    q = (
        select(MailMessage.thread_id, last.label("last"))
        .where(*conds)
        .group_by(MailMessage.thread_id)
    )
    if cursor:
        when, tid = decode_cursor(cursor)
        q = q.having(or_(last < when, and_(last == when, MailMessage.thread_id < tid)))
    groups = db.execute(
        q.order_by(last.desc(), MailMessage.thread_id.desc()).limit(limit + 1)
    ).all()
    more = len(groups) > limit
    groups = groups[:limit]
    tids = [g.thread_id for g in groups]
    by_thread: dict[str, list[MailMessage]] = defaultdict(list)
    if tids:
        for m in db.scalars(
            select(MailMessage)
            .where(*conds, MailMessage.thread_id.in_(tids))
            .order_by(MailMessage.date.desc(), MailMessage.id.desc())
        ):
            by_thread[m.thread_id].append(m)
    rows = []
    for g in groups:
        msgs = by_thread[g.thread_id]
        newest = msgs[0]
        rows.append(
            ThreadRow(
                thread_id=g.thread_id,
                count=len(msgs),
                last_date=g.last,
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
    nxt = encode_cursor(groups[-1].last, groups[-1].thread_id) if more and groups else None
    return rows, nxt


def totals(db: Session, conds: list) -> tuple[int, int]:
    count, size = db.execute(
        select(func.count(), func.coalesce(func.sum(MailMessage.size), 0)).where(*conds)
    ).one()
    return int(count), int(size)


def _label_tally(db: Session, account_id: int) -> dict[str, int]:
    rows = db.execute(
        text(
            "SELECT j.value, count(*) FROM mail_messages m, json_each(m.labels) j "
            "WHERE m.account_id = :a AND m.state != :archive GROUP BY j.value"
        ),
        {"a": account_id, "archive": MailState.LOCAL_ARCHIVE.value},
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
    archived = states.pop(MailState.LOCAL_ARCHIVE, 0)
    suggested = (
        db.scalar(
            select(func.count(func.distinct(RuleDecision.message_id)))
            .join(MailMessage, MailMessage.id == RuleDecision.message_id)
            .where(
                RuleDecision.account_id == account_id,
                RuleDecision.status == DecisionStatus.SUGGESTED,
                MailMessage.state != MailState.LOCAL_ARCHIVE,
            )
        )
        or 0
    )
    return {
        "all": sum(states.values()),
        "inbox": tally.get("INBOX", 0),
        "sent": tally.get("SENT", 0),
        "starred": tally.get("STARRED", 0),
        "important": tally.get("IMPORTANT", 0),
        "offloaded": states.get(MailState.OFFLOADED, 0),
        "deleted": states.get(MailState.DELETED, 0),
        "archive": archived,
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
