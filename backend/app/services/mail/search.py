"""The mail search language (spec §6.3). The reader's search box and the offload filter
(spec §5.1) both use this parser, so "offload these" can start from a search."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Text, cast, column, false, or_, select, text
from sqlalchemy.orm import Session

from app.models import MailLabel, MailMessage, RuleDecision
from app.models.enums import DecisionStatus, MailState

UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
AGE_DAYS = {"d": 1, "m": 30, "y": 365}
SYSTEM_LABELS = {
    "inbox": "INBOX",
    "sent": "SENT",
    "starred": "STARRED",
    "important": "IMPORTANT",
    "unread": "UNREAD",
}
VIEWS = {
    "all": "",
    "inbox": "INBOX",
    "sent": "SENT",
    "starred": "STARRED",
    "important": "IMPORTANT",
    "offloaded": MailState.OFFLOADED,
    "deleted": MailState.DELETED,
    "suggested": "suggested",
    "archive": MailState.LOCAL_ARCHIVE,
}
IS_VALUES = ("unread", "starred", "offloaded", "deleted", "archived")
OPERATORS = (
    "from",
    "to",
    "label",
    "has",
    "larger",
    "smaller",
    "before",
    "after",
    "older_than",
    "newer_than",
    "is",
)
_TOKEN = re.compile(r'(?:[A-Za-z_]+:)?"[^"]*"?|\S+')
MAX_SIZE = 2**63 - 1  # the biggest number SQLite can compare with
_SIZE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([kmg]?)b?", re.I)
_AGE = re.compile(r"([0-9]+)([dmy])", re.I)
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class SearchError(ValueError):
    pass


@dataclass
class MailQuery:
    words: list[str] = field(default_factory=list)
    senders: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    has_attachment: bool = False
    larger: int | None = None
    smaller: int | None = None
    before: datetime | None = None
    after: datetime | None = None
    unread: bool = False
    starred: bool = False
    offloaded: bool = False
    deleted: bool = False
    archived: bool = False

    def is_empty(self) -> bool:
        return self == MailQuery()


def _unquote(value: str) -> str:
    if value.startswith('"'):
        value = value[1:]
        if value.endswith('"'):
            value = value[:-1]
    return value


def parse_size(value: str) -> int:
    m = _SIZE.fullmatch(value.strip())
    if m is None:
        raise SearchError(f'"{value}" is not a size. Use a number with K, M or G, like 5M.')
    # Decimal, not float: a huge number must cap, not overflow.
    return min(int(Decimal(m.group(1)) * UNITS[m.group(2).lower()]), MAX_SIZE)


def parse_age(value: str) -> timedelta:
    m = _AGE.fullmatch(value.strip())
    msg = f'"{value}" is not an age. Use days, months or years, like 30d, 6m or 1y.'
    if m is None:
        raise SearchError(msg)
    try:
        return timedelta(days=int(m.group(1)) * AGE_DAYS[m.group(2).lower()])
    except (OverflowError, ValueError):
        raise SearchError(msg) from None


def _ago(now: datetime, value: str) -> datetime:
    try:
        return now - parse_age(value)
    except OverflowError:  # further back than the calendar goes
        raise SearchError(
            f'"{value}" is not an age. Use days, months or years, like 30d, 6m or 1y.'
        ) from None


def parse_day(value: str) -> datetime:
    msg = f'"{value}" is not a date. Use YYYY-MM-DD, like 2024-01-31.'
    if not _DAY.fullmatch(value):
        raise SearchError(msg)
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        raise SearchError(msg) from None


def _earlier(a: datetime | None, b: datetime) -> datetime:
    return b if a is None else min(a, b)


def _later(a: datetime | None, b: datetime) -> datetime:
    return b if a is None else max(a, b)


def parse_query(text: str, *, now: datetime) -> MailQuery:
    q = MailQuery()
    # A NUL byte would cut the search short inside SQLite.
    for tok in _TOKEN.findall((text or "").replace("\x00", " ")):
        key, sep, value = tok.partition(":")
        op = key.lower()
        if not sep or op not in OPERATORS or key.startswith('"'):
            word = _unquote(tok).strip()
            if word:
                q.words.append(word)
            continue
        value = _unquote(value).strip()
        if not value:
            raise SearchError(f'"{key}:" needs something after it.')
        if op == "from":
            q.senders.append(value)
        elif op == "to":
            q.recipients.append(value)
        elif op == "label":
            q.labels.append(value)
        elif op == "has":
            if value.lower() not in ("attachment", "attachments"):
                raise SearchError('"has:" only knows has:attachment.')
            q.has_attachment = True
        elif op == "larger":
            q.larger = parse_size(value)
        elif op == "smaller":
            q.smaller = parse_size(value)
        elif op == "before":
            q.before = _earlier(q.before, parse_day(value))
        elif op == "after":
            q.after = _later(q.after, parse_day(value))
        elif op == "older_than":
            q.before = _earlier(q.before, _ago(now, value))
        elif op == "newer_than":
            q.after = _later(q.after, _ago(now, value))
        else:  # is:
            v = value.lower()
            if v not in IS_VALUES:
                raise SearchError('"is:" knows unread, starred, offloaded and deleted.')
            setattr(q, v, True)
    return q


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def has_label(label_id: str):
    """Labels are stored as a JSON list of ids: look for the quoted id."""
    return cast(MailMessage.labels, Text).like(f'%"{_like(label_id)}"%', escape="\\")


def label_ids(db: Session, account_id: int, name: str) -> list[str]:
    """Names are matched in Python with casefold(): SQLite's lower() only knows A–Z, so
    "Équipe" would never match "équipe" there."""
    folded = name.casefold()
    ids = [SYSTEM_LABELS[folded]] if folded in SYSTEM_LABELS else []
    labels = db.execute(
        select(MailLabel.provider_label_id, MailLabel.name).where(
            MailLabel.account_id == account_id
        )
    ).all()
    ids += [
        lid
        for lid, lname in labels
        if lid not in ids and (lname.casefold() == folded or lid == name)
    ]
    return ids


def fts_match(words: list[str]) -> str:
    """Every word must match the start of a word in subject, from, to or body."""
    clean = [w.replace("\x00", " ").replace('"', '""') for w in words]
    return " ".join(f'"{w}"*' for w in clean)


def query_conditions(db: Session, account_id: int, q: MailQuery, *, archive: bool = False) -> list:
    conds: list = []
    if q.archived and not archive:  # inside the archive the view already limits to it
        raise SearchError("Archived mail is on the Local archive page.")
    for s in q.senders:
        conds.append(MailMessage.from_addr.ilike(f"%{_like(s)}%", escape="\\"))
    for r in q.recipients:
        pattern = f"%{_like(r)}%"
        conds.append(
            or_(
                MailMessage.to_addrs.ilike(pattern, escape="\\"),
                MailMessage.cc_addrs.ilike(pattern, escape="\\"),
            )
        )
    for name in q.labels:
        ids = label_ids(db, account_id, name)
        conds.append(or_(*[has_label(i) for i in ids]) if ids else false())
    if q.has_attachment:
        conds.append(MailMessage.has_attachments.is_(True))
    if q.larger is not None:
        conds.append(MailMessage.size > q.larger)
    if q.smaller is not None:
        conds.append(MailMessage.size < q.smaller)
    if q.before is not None:
        conds.append(MailMessage.date < q.before)
    if q.after is not None:
        conds.append(MailMessage.date >= q.after)
    if q.unread:
        conds.append(MailMessage.unread.is_(True))
    if q.starred:
        conds.append(MailMessage.starred.is_(True))
    if q.offloaded:
        conds.append(MailMessage.state == MailState.OFFLOADED)
    if q.deleted:
        conds.append(MailMessage.state == MailState.DELETED)
    if q.words:
        matches = (
            text("SELECT rowid FROM mail_fts WHERE mail_fts MATCH :fts")
            .bindparams(fts=fts_match(q.words))
            .columns(column("rowid"))
        )
        conds.append(MailMessage.id.in_(matches))
    return conds


def view_conditions(view: str, label: str | None) -> list:
    if view not in VIEWS:
        raise SearchError(f"unknown view {view!r}")
    conds: list = []
    target = VIEWS[view]
    if view in ("offloaded", "deleted"):
        conds.append(MailMessage.state == target)
    elif view == "archive":
        pass  # mail_conditions limits to the archive
    elif view == "suggested":
        conds.append(
            MailMessage.id.in_(
                select(RuleDecision.message_id).where(
                    RuleDecision.status == DecisionStatus.SUGGESTED
                )
            )
        )
    elif target:
        conds.append(has_label(target))
    if label:
        conds.append(has_label(label))
    return conds


def mail_conditions(
    db: Session,
    account_id: int,
    *,
    view: str = "all",
    label: str | None = None,
    q: str = "",
    now: datetime,
    archive: bool = False,
) -> list:
    """Every condition for one account, view (or label) and search, ready for .where(*…).
    Local-archive mail is left out, unless asked for (archive=True or the archive view)."""
    in_archive = archive or view == "archive"
    return [
        MailMessage.account_id == account_id,
        MailMessage.state == MailState.LOCAL_ARCHIVE
        if in_archive
        else MailMessage.state != MailState.LOCAL_ARCHIVE,
        *view_conditions(view, label),
        *query_conditions(db, account_id, parse_query(q, now=now), archive=in_archive),
    ]
