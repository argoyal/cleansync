"""The mail search language (spec §6.3). The reader's search box and the offload filter
(spec §5.1) both use this parser, so "offload these" can start from a search."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import column, false, not_, or_, select, text
from sqlalchemy.orm import Session

from app.models import MailLabel, MailMessage, MailMessageLabel, RuleDecision
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
GMAIL_LABEL_VIEWS = {
    "inbox": "INBOX",
    "sent": "SENT",
    "starred": "STARRED",
    "important": "IMPORTANT",
}
# Views of mail that is in Gmail (mail panel spec §4): they only ever show state IN_MAIL.
GMAIL_VIEWS = frozenset({"all", "archived", "suggested", *GMAIL_LABEL_VIEWS})
# Views of mail CleanSync keeps that is out of Gmail. "kept" is both, for Restore to Gmail.
OUT_OF_GMAIL = {
    "offloaded": (MailState.OFFLOADED,),
    "deleted": (MailState.DELETED,),
    "kept": (MailState.OFFLOADED, MailState.DELETED),
}
VIEWS = GMAIL_VIEWS | set(OUT_OF_GMAIL)
VIEW_ALIASES = {"archive": "offloaded"}  # old links to the Local archive
IS_VALUES = ("unread", "starred", "offloaded", "deleted")
OFFLOADED_ELSEWHERE_MSG = "Offloaded mail is in the Offloaded view."
DELETED_ELSEWHERE_MSG = "Mail deleted in Gmail is in the Deleted in Gmail view."
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
    "in",
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
    in_archived: bool = False

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
        elif op == "in":
            if value.lower() != "archived":
                raise SearchError('"in:" only knows in:archived.')
            q.in_archived = True
        else:  # is:
            v = value.lower()
            if v not in IS_VALUES:
                raise SearchError('"is:" knows unread, starred, offloaded and deleted.')
            setattr(q, v, True)
    return q


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def has_label(account_id: int, label_id: str):
    """Mails of this account that carry this label id, read from the label table the
    database keeps up to date (issue #16), not from each mail's JSON. A lookup per mail on
    the table's key, so the list can walk the date index newest first and stop early."""
    return (
        select(MailMessageLabel.message_id)
        .where(
            MailMessageLabel.message_id == MailMessage.id,
            MailMessageLabel.label_id == label_id,
            MailMessageLabel.account_id == account_id,
        )
        .exists()
    )


def archived_conditions(account_id: int) -> list:
    """Gmail's archive (mail panel spec §4): neither in the Inbox nor sent by the owner."""
    return [not_(has_label(account_id, "INBOX")), not_(has_label(account_id, "SENT"))]


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


def query_conditions(db: Session, account_id: int, q: MailQuery, *, view: str = "all") -> list:
    conds: list = []
    if view in GMAIL_VIEWS:
        if q.offloaded:
            raise SearchError(OFFLOADED_ELSEWHERE_MSG)
        if q.deleted:
            raise SearchError(DELETED_ELSEWHERE_MSG)
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
        conds.append(or_(*[has_label(account_id, i) for i in ids]) if ids else false())
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
    if q.in_archived:
        conds += [MailMessage.state == MailState.IN_MAIL, *archived_conditions(account_id)]
    if q.words:
        matches = (
            text("SELECT rowid FROM mail_fts WHERE mail_fts MATCH :fts")
            .bindparams(fts=fts_match(q.words))
            .columns(column("rowid"))
        )
        conds.append(MailMessage.id.in_(matches))
    return conds


def view_conditions(account_id: int, view: str, label: str | None) -> list:
    if view not in VIEWS:
        raise SearchError(f"unknown view {view!r}")
    conds: list = []
    if view in GMAIL_VIEWS:
        conds.append(MailMessage.state == MailState.IN_MAIL)
    elif view in OUT_OF_GMAIL:
        conds.append(MailMessage.state.in_(OUT_OF_GMAIL[view]))
    if view in GMAIL_LABEL_VIEWS:
        conds.append(has_label(account_id, GMAIL_LABEL_VIEWS[view]))
    elif view == "archived":
        conds += archived_conditions(account_id)
    elif view == "suggested":
        conds.append(
            MailMessage.id.in_(
                select(RuleDecision.message_id).where(
                    RuleDecision.status == DecisionStatus.SUGGESTED
                )
            )
        )
    if label:
        conds.append(has_label(account_id, label))
    return conds


def mail_conditions(
    db: Session,
    account_id: int,
    *,
    view: str = "all",
    label: str | None = None,
    q: str = "",
    now: datetime,
) -> list:
    """Every condition for one account, view (or label) and search, ready for .where(*…).
    Gmail views hold only mail in Gmail (mail panel spec §4)."""
    view = VIEW_ALIASES.get(view, view)
    return [
        MailMessage.account_id == account_id,
        *view_conditions(account_id, view, label),
        *query_conditions(db, account_id, parse_query(q, now=now), view=view),
    ]
