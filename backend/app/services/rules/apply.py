# backend/app/services/rules/apply.py
"""Applying a decision to Gmail, and undoing it (AI rules spec §6, §7). The exact label
changes are recorded on the decision before Gmail is called; the local row follows at once.
Delete checks the local copy, moves the mail to Gmail's trash and into the local archive."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import Account, MailLabel, MailMessage, RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, MailState
from app.paths import Layout
from app.services.google.client import RateLimited
from app.services.google.quota import BudgetReached
from app.services.mail.adapter import MailAdapter, MailApiError, MailNotFound
from app.services.mail.index import save_labels, set_flags
from app.services.mail.store import check_eml, eml_path

BUDGET_WAIT = "Google's daily budget is used up; this is applied when it resets."
GONE_MSG = "It's no longer in Gmail."
NOT_IN_MAIL_MSG = "It's no longer in Gmail, so nothing was changed."
APPLYABLE = (DecisionStatus.SUGGESTED, DecisionStatus.PLANNED)
SYSTEM_NAMES = {"INBOX": "Inbox", "UNREAD": "Unread", "STARRED": "Starred", "TRASH": "Trash"}


@dataclass
class Planned:
    add_names: list[str]
    remove: list[str]
    trash: bool


@dataclass
class UndoResult:
    status: str
    left: list[str]


def plan_changes(actions: list[dict], labels_now: list[str]) -> Planned:
    now = set(labels_now)
    add: list[str] = []
    remove: list[str] = []
    trash = False
    for a in actions:
        kind = a.get("kind")
        if kind == "archive" and "INBOX" in now:
            remove.append("INBOX")
        elif kind == "mark_read" and "UNREAD" in now:
            remove.append("UNREAD")
        elif kind == "star" and "STARRED" not in now:
            add.append("STARRED")
        elif kind == "label":
            add.append(str(a["label"]))
        elif kind == "delete":
            trash = True
    return Planned(list(dict.fromkeys(add)), list(dict.fromkeys(remove)), trash)


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


class Applier:
    def __init__(
        self,
        *,
        session_factory,
        layout: Layout,
        adapter_for: Callable[[Account], MailAdapter],
        clock=utcnow,
    ) -> None:
        self.sf = session_factory
        self.layout = layout
        self.adapter_for = adapter_for
        self.clock = clock
        self._label_lock = asyncio.Lock()  # one label creation at a time: never two copies

    def _local_label(self, db: Session, account_id: int, name: str) -> str | None:
        want = _norm(name)
        for lab in db.scalars(select(MailLabel).where(MailLabel.account_id == account_id)):
            if _norm(lab.name) == want:
                return lab.provider_label_id
        return None

    async def _label_id(self, adapter: MailAdapter, account_id: int, name: str) -> str:
        if name in SYSTEM_NAMES:  # STARRED and friends are ids already
            return name
        async with self._label_lock:
            with self.sf() as db:
                found = self._local_label(db, account_id, name)
            if found:
                return found
            try:
                lab = await adapter.create_label(" ".join(name.split()))
                with self.sf() as db:
                    db.add(
                        MailLabel(
                            account_id=account_id,
                            provider_label_id=lab.id,
                            name=lab.name,
                            type="user",
                        )
                    )
                    db.commit()
                return lab.id
            except MailApiError as e:
                if e.status != 409:
                    raise
            # Made in Gmail meanwhile (or by hand): read Gmail's list once and use it.
            labels = await adapter.labels()
            with self.sf() as db:
                save_labels(db, account_id, labels)
                found = self._local_label(db, account_id, name)
            if not found:
                raise MailApiError(f"Gmail wouldn't make the label {name!r}.", 409)
            return found

    def _fail(self, decision_id: int, reason: str) -> str:
        with self.sf() as db:
            d = db.get(RuleDecision, decision_id)
            d.status, d.reason = DecisionStatus.FAILED, reason
            db.commit()
        return DecisionStatus.FAILED

    async def apply(self, decision_id: int) -> str:
        with self.sf() as db:
            d = db.get(RuleDecision, decision_id)
            if d is None or d.status not in APPLYABLE:
                return d.status if d else DecisionStatus.FAILED
            row = db.get(MailMessage, d.message_id)
            acc = db.get(Account, d.account_id)
            actions, labels_now = list(d.actions or []), list(row.labels or [])
            state, pid, path_rel = row.state, row.provider_id, row.file_path
            size, sha = row.file_size, row.sha256
        if state != MailState.IN_MAIL:
            return self._fail(decision_id, NOT_IN_MAIL_MSG)
        planned = plan_changes(actions, labels_now)
        if planned.trash:
            problem = await asyncio.to_thread(
                check_eml, eml_path(self.layout, acc.email, path_rel), size, sha
            )
            if problem:
                return self._fail(
                    decision_id,
                    f"The copy here didn't check out ({problem}), so nothing was deleted.",
                )
        adapter = self.adapter_for(acc)
        try:
            add = [await self._label_id(adapter, acc.id, n) for n in planned.add_names]
            changes = {"add": add, "remove": planned.remove, "trash": planned.trash}
            with self.sf() as db:
                db.get(RuleDecision, decision_id).changes = changes
                db.commit()
            if planned.trash:
                await adapter.trash(pid)
                new_labels = [x for x in labels_now if x != "INBOX"] + ["TRASH"]
            elif add or planned.remove:
                new_labels = list(await adapter.modify(pid, add, planned.remove))
            else:
                new_labels = labels_now
        except (BudgetReached, RateLimited):
            with self.sf() as db:
                db.get(RuleDecision, decision_id).reason = BUDGET_WAIT
                db.commit()
            raise
        except MailNotFound:
            return self._fail(decision_id, GONE_MSG)
        except MailApiError as e:
            return self._fail(decision_id, str(e))
        with self.sf() as db:
            d = db.get(RuleDecision, decision_id)
            row = db.get(MailMessage, d.message_id)
            set_flags(row, new_labels)
            if planned.trash:
                row.state = MailState.LOCAL_ARCHIVE
            d.status, d.reason, d.applied_at = DecisionStatus.APPLIED, None, self.clock()
            db.commit()
        return DecisionStatus.APPLIED

    async def undo(self, decision_id: int) -> UndoResult:
        with self.sf() as db:
            d = db.get(RuleDecision, decision_id)
            if d is None or d.status != DecisionStatus.APPLIED:
                return UndoResult(d.status if d else DecisionStatus.FAILED, [])
            row = db.get(MailMessage, d.message_id)
            acc = db.get(Account, d.account_id)
            changes = dict(d.changes or {})
            labels_now, state, pid = set(row.labels or []), row.state, row.provider_id
            seen = list(d.labels_seen or [])
        adapter = self.adapter_for(acc)
        left: list[str] = []
        try:
            if changes.get("trash"):
                if state == MailState.LOCAL_ARCHIVE:
                    await adapter.untrash(pid)
                    new_labels, new_state = seen, MailState.IN_MAIL
                else:
                    left.append("TRASH")
                    new_labels, new_state = list(labels_now), state
            else:
                back = [x for x in changes.get("remove") or [] if x not in labels_now]
                drop = [x for x in changes.get("add") or [] if x in labels_now]
                left = [x for x in changes.get("remove") or [] if x in labels_now] + [
                    x for x in changes.get("add") or [] if x not in labels_now
                ]
                new_labels = (
                    list(await adapter.modify(pid, back, drop))
                    if back or drop
                    else list(labels_now)
                )
                new_state = state
        except MailNotFound:
            with self.sf() as db:
                db.get(RuleDecision, decision_id).reason = "Couldn't undo: " + GONE_MSG
                db.commit()
            return UndoResult(DecisionStatus.APPLIED, [])
        with self.sf() as db:
            d = db.get(RuleDecision, decision_id)
            row = db.get(MailMessage, d.message_id)
            set_flags(row, new_labels)
            row.state = new_state
            d.status = DecisionStatus.UNDONE
            d.reason = (
                "Left as you set them since: " + ", ".join(SYSTEM_NAMES.get(x, x) for x in left)
                if left
                else None
            )
            db.commit()
        return UndoResult(DecisionStatus.UNDONE, left)

    async def delete_by_hand(self, account_id: int, message_id: int) -> RuleDecision:
        with self.sf() as db:
            row = db.get(MailMessage, message_id)
            if row is None or row.account_id != account_id:
                raise LookupError("That mail isn't here.")
            d = RuleDecision(
                account_id=account_id,
                message_id=message_id,
                how=DecisionHow.MANUAL,
                why="Deleted by you.",
                actions=[{"kind": "delete"}],
                labels_seen=list(row.labels or []),
                status=DecisionStatus.SUGGESTED,
            )
            db.add(d)
            db.commit()
            did = d.id
        await self.apply(did)
        with self.sf() as db:
            return db.get(RuleDecision, did)
