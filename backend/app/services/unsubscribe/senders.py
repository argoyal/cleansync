"""The unsubscribe sender list (0.5.2 §3.2, §3.3): one GROUP BY over mail_messages.

SQLite's bare-column rule does the "newest mail" part: in an aggregate with a single MAX(),
the bare columns come from the row that holds the max. So the name and the link columns come
from the newest mail that has a link."""

# ruff: noqa: S608  (the only text joined into the SQL is fixed pieces from the tables above;
# every value from a request is a bound parameter)

from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import UnsubscribeSender
from app.models.enums import MailState

TABS = ("suggested", "all", "unsubscribed", "kept", "auto_archived")
SORTS = ("mails", "unread", "newest", "size")
KEEP = object()  # set_status: leave filter_id as it is

_READ_N = "SUM(CASE WHEN m.unread THEN 0 ELSE 1 END)"
_HAVING = {
    "suggested": f"HAVING COUNT(*) >= 3 AND {_READ_N} * 5 < COUNT(*) AND s.status IS NULL",
    "all": "",
    "unsubscribed": "HAVING s.status = :status",
    "kept": "HAVING s.status = :status",
    "auto_archived": "HAVING s.status = :status",
}
_ORDER = {
    "mails": "ORDER BY count DESC, address",
    "unread": "ORDER BY (count - read_n) DESC, address",
    "newest": "ORDER BY last_at DESC, address",
    "size": "ORDER BY bytes DESC, address",
}

SENDERS_SQL = f"""
SELECT m.sender AS address,
       MAX(m.date) AS last_at,
       m.from_addr AS from_addr,
       m.list_unsubscribe AS newest_unsubscribe,
       m.list_unsubscribe_post AS newest_unsubscribe_post,
       m.id AS newest_mail_id,
       COUNT(*) AS count,
       {_READ_N} AS read_n,
       SUM(CASE WHEN m.state = :in_mail AND instr(m.labels, '"INBOX"') > 0 THEN 1 ELSE 0 END)
           AS inbox_n,
       SUM(m.size) AS bytes,
       s.status AS status, s.method AS method, s.note AS note
FROM mail_messages m
LEFT JOIN unsubscribe_senders s ON s.account_id = m.account_id AND s.address = m.sender
WHERE m.account_id = :account_id
  AND m.list_unsubscribe IS NOT NULL
  AND m.sender IS NOT NULL
  AND m.sender != :own
  AND instr(m.labels, '"SENT"') = 0
  AND instr(m.labels, '"DRAFT"') = 0
  {{search}}
GROUP BY m.sender
{{having}}
"""


@dataclass
class SenderRow:
    address: str
    name: str
    count: int
    read_pct: int
    inbox_pct: int
    last_at: datetime
    bytes: int
    status: str | None
    method: str | None
    note: str | None
    newest_unsubscribe: str
    newest_unsubscribe_post: str | None
    newest_mail_id: int


def _build(account_id, own_email, tab, q):
    if tab not in TABS:
        raise ValueError(f"unknown tab {tab!r}")
    params: dict = {
        "account_id": account_id,
        "own": (own_email or "").lower(),
        "in_mail": MailState.IN_MAIL.value,
    }
    search = ""
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["q"] = f"%{escaped}%"
        search = "AND (m.sender LIKE :q ESCAPE '\\' OR m.from_addr LIKE :q ESCAPE '\\')"
    if tab in ("unsubscribed", "kept", "auto_archived"):
        params["status"] = tab
    return SENDERS_SQL.format(search=search, having=_HAVING[tab]), params


def _row(r) -> SenderRow:
    last_at = r.last_at
    if isinstance(last_at, str):
        last_at = datetime.fromisoformat(last_at)
    return SenderRow(
        address=r.address,
        name=parseaddr(r.from_addr or "")[0] or r.address,
        count=r.count,
        read_pct=round(100 * r.read_n / r.count),
        inbox_pct=round(100 * r.inbox_n / r.count),
        last_at=last_at,
        bytes=r.bytes or 0,
        status=r.status,
        method=r.method,
        note=r.note,
        newest_unsubscribe=r.newest_unsubscribe,
        newest_unsubscribe_post=r.newest_unsubscribe_post,
        newest_mail_id=r.newest_mail_id,
    )


def list_senders(
    db: Session,
    account_id: int,
    own_email: str,
    *,
    tab: str = "suggested",
    q: str = "",
    sort: str = "mails",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SenderRow], int]:
    if sort not in SORTS:
        raise ValueError(f"unknown sort {sort!r}")
    sql, params = _build(account_id, own_email, tab, q)
    total = db.execute(text(f"SELECT COUNT(*) FROM ({sql})"), params).scalar() or 0
    params |= {"limit": page_size, "offset": (max(page, 1) - 1) * page_size}
    result = db.execute(text(f"{sql} {_ORDER[sort]} LIMIT :limit OFFSET :offset"), params)
    return [_row(r) for r in result], total


def sender_addresses(
    db: Session, account_id: int, own_email: str, *, tab: str, q: str = ""
) -> list[str]:
    sql, params = _build(account_id, own_email, tab, q)
    return [r[0] for r in db.execute(text(f"SELECT address FROM ({sql}) ORDER BY address"), params)]


def suggested_count(db: Session, account_id: int, own_email: str) -> int:
    sql, params = _build(account_id, own_email, "suggested", "")
    return db.execute(text(f"SELECT COUNT(*) FROM ({sql})"), params).scalar() or 0


def newest_with_link(
    db: Session, account_id: int, own_email: str, address: str
) -> SenderRow | None:
    sql, params = _build(account_id, own_email, "all", address.lower())
    params["q"] = address.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    # An exact match: the LIKE above only narrows the work.
    for r in db.execute(text(sql), params):
        if r.address == address.lower():
            return _row(r)
    return None


def set_status(
    db: Session,
    account_id: int,
    address: str,
    status: str | None,
    *,
    method: str | None = None,
    note: str | None = None,
    filter_id=KEEP,
    now: datetime,
    commit: bool = True,
) -> None:
    """Set or clear (status=None) a sender's status. The row is never deleted: it holds
    filter_id."""
    address = address.lower()
    row = db.scalar(
        select(UnsubscribeSender).where(
            UnsubscribeSender.account_id == account_id, UnsubscribeSender.address == address
        )
    )
    if row is None:
        row = UnsubscribeSender(account_id=account_id, address=address)
        db.add(row)
    row.status = status
    row.method = method
    row.note = note
    if filter_id is not KEEP:
        row.filter_id = filter_id
    row.updated_at = now
    if commit:
        db.commit()
