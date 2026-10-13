"""Delete forever (mail panel spec §7): removes CleanSync's own copy of mail already out of
Gmail (Offloaded, or Deleted in Gmail): the .eml, the search entry, the rule history and the
row. A short record goes to Activity. Mail still in Gmail needs round 3."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MailJob, MailMessage
from app.models.enums import JobState, MailJobKind, MailState
from app.paths import Layout
from app.services.events.bus import EventBus, record_event
from app.services.mail.index import delete_search_rows
from app.services.mail.offload_plan import rows_where_in
from app.services.mail.search import VIEW_ALIASES, SearchError, mail_conditions
from app.services.mail.store import eml_path

MAX_RECORDED = 500
FOREVER_VIEWS = {"offloaded": MailState.OFFLOADED, "deleted": MailState.DELETED}
VIEW_NAMES = {"offloaded": "Offloaded", "deleted": "Deleted in Gmail"}
RESTORE_RUNNING_MSG = "A restore is running for this mailbox. Try again when it's done."
NOTHING_PICKED_MSG = "Pick mails or type a search."
WRONG_VIEW_MSG = "Delete forever works in Offloaded and Deleted in Gmail."
NONE_HERE_MSG = "None of these mails are in {name}."
log = logging.getLogger(__name__)


class ForeverProblem(ValueError):
    pass


@dataclass
class ForeverResult:
    deleted: int
    left: int


def _mails(n: int) -> str:
    return f"{n:,} {'mail' if n == 1 else 'mails'}"


def forever_targets(
    db: Session,
    account_id: int,
    *,
    view: str,
    label: str | None,
    q: str,
    message_ids: list[int] | None,
    now: datetime,
) -> tuple[list[MailMessage], int]:
    """The mails to remove, and how many picked ones were left because they aren't in the
    view. A pick by hand, even an empty one, never falls back to the search."""
    state = FOREVER_VIEWS.get(view)
    if state is None:
        raise ForeverProblem(WRONG_VIEW_MSG)
    if message_ids is not None:
        if not message_ids:
            raise ForeverProblem(NOTHING_PICKED_MSG)
        rows = rows_where_in(db, account_id, MailMessage.id, message_ids)
        mine = [r for r in rows if r.state == state]
        return mine, len(rows) - len(mine)
    try:
        conds = mail_conditions(db, account_id, view=view, label=label, q=q, now=now)
    except SearchError as e:
        raise ForeverProblem(str(e)) from None
    return list(db.scalars(select(MailMessage).where(*conds))), 0


def delete_forever(
    db: Session,
    layout: Layout,
    bus: EventBus,
    account_id: int,
    email: str,
    *,
    view: str,
    label: str | None = None,
    q: str = "",
    message_ids: list[int] | None = None,
    confirm: int,
    now: datetime,
    restore_queued: Callable[[int], bool] | None = None,
) -> ForeverResult:
    """restore_queued(account_id): whether a restore waits in the queue (it has no job row
    until it starts)."""
    running = db.scalar(
        select(MailJob.id)
        .where(
            MailJob.account_id == account_id,
            MailJob.kind == MailJobKind.RESTORE,
            MailJob.state.in_([JobState.QUEUED, JobState.RUNNING, JobState.PAUSED]),
        )
        .limit(1)
    )
    if running is not None or (restore_queued is not None and restore_queued(account_id)):
        raise ForeverProblem(RESTORE_RUNNING_MSG)
    view = VIEW_ALIASES.get(view, view)
    rows, left = forever_targets(
        db, account_id, view=view, label=label, q=q, message_ids=message_ids, now=now
    )
    name = VIEW_NAMES[view]
    if not rows:
        raise ForeverProblem(NONE_HERE_MSG.format(name=name))
    if confirm != len(rows):
        raise ForeverProblem(f"The number you typed doesn't match the {_mails(len(rows))} picked.")
    record = [
        {"from": r.from_addr, "subject": r.subject, "date": r.date.isoformat()}
        for r in rows[:MAX_RECORDED]
    ]
    paths = [eml_path(layout, email, r.file_path) for r in rows]
    delete_search_rows(db, [r.id for r in rows], commit=False)  # one commit: rows, search, record
    for r in rows:
        db.delete(r)  # its decisions, to-do and offload items go with it (ON DELETE CASCADE)
    record_event(
        db,
        bus,
        kind="mail_deleted_forever",
        message=f"Deleted {_mails(len(rows))} forever from {name}",
        account_id=account_id,
        data={"mails": record, "more": max(0, len(rows) - MAX_RECORDED)},
    )  # commits
    for p in paths:  # after the commit: a leftover file is harmless, a row without one isn't
        try:
            p.unlink(missing_ok=True)
        except OSError as e:
            log.warning("could not remove %s: %s", p.name, e)
    return ForeverResult(len(rows), left)
