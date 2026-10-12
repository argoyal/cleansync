"""The local archive (AI rules spec §7): mail deleted from Gmail by a rule or by hand, kept
here only. Delete forever removes CleanSync's own copy, leaving a short record on Activity."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MailMessage
from app.models.enums import MailState
from app.paths import Layout
from app.services.events.bus import EventBus, record_event
from app.services.mail.index import delete_search_rows
from app.services.mail.offload_plan import rows_where_in
from app.services.mail.search import SearchError, mail_conditions
from app.services.mail.store import eml_path

MAX_RECORDED = 500


class ArchiveProblem(ValueError):
    pass


@dataclass
class ForeverResult:
    deleted: int
    left: int


def archive_targets(
    db: Session, account_id: int, *, message_ids: list[int] | None, q: str, now: datetime
) -> tuple[list[MailMessage], int]:
    if message_ids:
        rows = rows_where_in(db, account_id, MailMessage.id, message_ids)
        mine = [r for r in rows if r.state == MailState.LOCAL_ARCHIVE]
        return mine, len(rows) - len(mine)
    try:
        conds = mail_conditions(db, account_id, q=q, now=now, archive=True)
    except SearchError as e:
        raise ArchiveProblem(str(e)) from None
    return list(db.scalars(select(MailMessage).where(*conds))), 0


def delete_forever(
    db: Session,
    layout: Layout,
    bus: EventBus,
    account_id: int,
    email: str,
    *,
    message_ids: list[int] | None,
    q: str,
    confirm: int,
    now: datetime,
) -> ForeverResult:
    rows, left = archive_targets(db, account_id, message_ids=message_ids, q=q, now=now)
    if not rows:
        raise ArchiveProblem("None of these mails are in the local archive.")
    if confirm != len(rows):
        raise ArchiveProblem(f"The number you typed doesn't match the {len(rows):,} mails picked.")
    record = [
        {"from": r.from_addr, "subject": r.subject, "date": r.date.isoformat()}
        for r in rows[:MAX_RECORDED]
    ]
    paths = [eml_path(layout, email, r.file_path) for r in rows]
    delete_search_rows(db, [r.id for r in rows])
    for r in rows:
        db.delete(r)
    record_event(
        db,
        bus,
        kind="mail_deleted_forever",
        message=f"Deleted {len(rows):,} {'mail' if len(rows) == 1 else 'mails'} forever from the local archive",
        account_id=account_id,
        data={"mails": record, "more": max(0, len(rows) - MAX_RECORDED)},
    )  # commits
    for p in paths:  # after the commit: a leftover file is harmless, a row without one isn't
        p.unlink(missing_ok=True)
    return ForeverResult(len(rows), left)
