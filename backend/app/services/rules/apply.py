# backend/app/services/rules/apply.py
"""Applying a decision to Gmail, and undoing it (AI rules spec §6, §7). The exact label
changes are recorded on the decision before Gmail is called; the local row follows at once.
Offload checks the local copy, then removes the mail from Gmail: for good when the mailbox
allows permanent delete, else to Gmail's trash (bulk offload spec §4)."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import Account, MailLabel, MailMessage, RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, MailState
from app.paths import Layout
from app.services.accounts.permanent import NOT_ALLOWED_MSG, permanent_delete_on, permission_lost
from app.services.events.bus import EventBus
from app.services.google.client import RateLimited
from app.services.google.quota import BudgetReached
from app.services.mail.adapter import (
    MailAdapter,
    MailApiError,
    MailAuthError,
    MailNotFound,
    MailUnreachable,
    PermanentDeleteRefused,
)
from app.services.mail.forever import (
    mail_record,
    record_rule_forever,
    remove_copies,
    unlink_quietly,
)
from app.services.mail.index import save_labels, set_flags
from app.services.mail.store import check_eml, eml_path

BUDGET_WAIT = "Google's daily budget is used up; this is applied when it resets."
GONE_MSG = "It's no longer in Gmail."
NOT_IN_MAIL_MSG = "It's no longer in Gmail, so nothing was changed."
NOT_UNDONE_MSG = "It's no longer in Gmail, so nothing was undone."
UNREACHABLE_WAIT = "Gmail couldn't be reached; this is tried again later."
RECONNECT_WAIT = "Reconnect this Google account; this is tried again after that."
WAITING_REASONS = (BUDGET_WAIT, UNREACHABLE_WAIT, RECONNECT_WAIT)
WAIT_ERRORS = (BudgetReached, RateLimited, MailUnreachable, MailAuthError)
GONE_ROW_MSG = "That mail isn't here any more."
NOT_UNDOABLE_MSG = (
    "It was removed from Gmail for good, so Undo can't bring it back. "
    "Use Restore to Gmail in Offloaded."
)
SYSTEM_LABELS = {
    "INBOX",
    "SPAM",
    "TRASH",
    "UNREAD",
    "STARRED",
    "IMPORTANT",
    "SENT",
    "DRAFT",
    "CHAT",
}
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
        elif kind == "offload":
            trash = True
    return Planned(list(dict.fromkeys(add)), list(dict.fromkeys(remove)), trash)


def is_forever(actions: list[dict] | None) -> bool:
    return any(isinstance(a, dict) and a.get("kind") == "delete_forever" for a in actions or [])


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def is_system_label(name: str) -> bool:
    """True when the name is one of Gmail's own labels (a rule must never add those)."""
    up = " ".join(name.split()).upper()
    return up in SYSTEM_LABELS or up.startswith("CATEGORY_")


def _wait_reason(e: Exception) -> str:
    if isinstance(e, MailUnreachable):
        return UNREACHABLE_WAIT
    if isinstance(e, MailAuthError):
        return RECONNECT_WAIT
    return BUDGET_WAIT


class Applier:
    def __init__(
        self,
        *,
        session_factory,
        layout: Layout,
        adapter_for: Callable[[Account], MailAdapter],
        bus: EventBus | None = None,
        clock=utcnow,
    ) -> None:
        self.sf = session_factory
        self.layout = layout
        self.adapter_for = adapter_for
        self.bus = bus or EventBus()
        self.clock = clock
        self._label_lock = asyncio.Lock()  # one label creation at a time: never two copies

    def _local_label(self, db: Session, account_id: int, name: str) -> str | None:
        want = _norm(name)
        for lab in db.scalars(
            select(MailLabel).where(MailLabel.account_id == account_id, MailLabel.type == "user")
        ):
            if _norm(lab.name) == want:
                return lab.provider_label_id
        return None

    async def _label_id(self, adapter: MailAdapter, account_id: int, name: str) -> str:
        if name == "STARRED":  # the one system id plan_changes produces itself
            return name
        if is_system_label(name):
            raise MailApiError(f"{name!r} is one of Gmail's own labels, so a rule can't add it.")
        async with self._label_lock:
            with self.sf() as db:
                found = self._local_label(db, account_id, name)
            if found:
                return found
            try:
                lab = await adapter.create_label(" ".join(name.split()))
                try:
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
                except IntegrityError:  # a backup saved the same label first
                    pass
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

    def _not_allowed(self, decision_id: int) -> str:
        """Permanent delete is off: a suggestion waits with the reason; a planned change of a
        run fails with it, so the run never comes back to it (plan decision 18)."""
        with self.sf() as db:
            d = db.get(RuleDecision, decision_id)
            d.reason = NOT_ALLOWED_MSG
            if d.status == DecisionStatus.PLANNED:
                d.status = DecisionStatus.FAILED
            db.commit()
            return d.status

    async def _apply_forever(self, decision_id: int, acc: Account) -> str:
        """Delete forever (bulk offload spec §6, §5): Gmail first, then CleanSync's copy. The
        decision goes with the mail (its rule history), so Activity keeps the record."""
        if not permanent_delete_on(acc):
            return self._not_allowed(decision_id)
        with self.sf() as db:
            d = db.get(RuleDecision, decision_id)
            msg_id, rule_name = d.message_id, d.rule_name or "A rule"
            pid = db.get(MailMessage, msg_id).provider_id
        adapter = self.adapter_for(acc)
        try:
            await adapter.delete(pid)
        except PermanentDeleteRefused:
            with self.sf() as db:
                permission_lost(db, self.bus, acc.id)
            return self._not_allowed(decision_id)
        except WAIT_ERRORS as e:
            with self.sf() as db:
                db.get(RuleDecision, decision_id).reason = _wait_reason(e)
                db.commit()
            raise
        except MailNotFound:
            pass  # already gone from Gmail: only the copy is left to remove
        except MailApiError as e:
            return self._fail(decision_id, str(e))
        with self.sf() as db:
            row = db.get(MailMessage, msg_id)
            if row is not None:
                record = mail_record([row])
                paths = remove_copies(db, self.layout, acc.email, [row])
                record_rule_forever(db, self.bus, acc.id, rule_name, record, self.clock())
                unlink_quietly(paths)  # after the commit
        return DecisionStatus.APPLIED

    async def _take_out(self, acc: Account, adapter: MailAdapter, pid: str) -> bool:
        """Offload's Gmail step (bulk offload spec §4): for good when the mailbox allows
        permanent delete, else to Gmail's trash. True when it was removed for good."""
        if permanent_delete_on(acc):
            try:
                await adapter.delete(pid)
                return True
            except PermanentDeleteRefused:
                with self.sf() as db:
                    permission_lost(db, self.bus, acc.id)
        await adapter.trash(pid)
        return False

    async def apply(self, decision_id: int) -> str:
        with self.sf() as db:
            d = db.get(RuleDecision, decision_id)
            if d is None or d.status not in APPLYABLE:
                return d.status if d else DecisionStatus.FAILED
            row = db.get(MailMessage, d.message_id)
            if row is None:
                d.status, d.reason = DecisionStatus.FAILED, GONE_ROW_MSG
                db.commit()
                return DecisionStatus.FAILED
            acc = db.get(Account, d.account_id)
            actions, labels_now = list(d.actions or []), list(row.labels or [])
            state, pid, path_rel = row.state, row.provider_id, row.file_path
            size, sha = row.file_size, row.sha256
        if state != MailState.IN_MAIL:
            return self._fail(decision_id, NOT_IN_MAIL_MSG)
        if is_forever(actions):
            return await self._apply_forever(decision_id, acc)
        planned = plan_changes(actions, labels_now)
        if planned.trash:
            problem = await asyncio.to_thread(
                check_eml, eml_path(self.layout, acc.email, path_rel), size, sha
            )
            if problem:
                return self._fail(
                    decision_id,
                    f"The copy here didn't check out ({problem}), so nothing was offloaded.",
                )
        removed = False
        adapter = self.adapter_for(acc)
        try:
            add = [await self._label_id(adapter, acc.id, n) for n in planned.add_names]
            add = [x for x in add if x not in labels_now]  # never undo what was already there
            changes = {"add": add, "remove": planned.remove, "trash": planned.trash}
            with self.sf() as db:
                db.get(RuleDecision, decision_id).changes = changes
                db.commit()
            if planned.trash:
                removed = await self._take_out(acc, adapter, pid)
                # The row keeps the labels Gmail had (Inbox included), trashed or not, so a
                # restore that has to import the mail again puts it back where it was.
                new_labels = labels_now
            elif add or planned.remove:
                new_labels = list(await adapter.modify(pid, add, planned.remove))
            else:
                new_labels = labels_now
        except WAIT_ERRORS as e:
            with self.sf() as db:
                db.get(RuleDecision, decision_id).reason = _wait_reason(e)
                db.commit()
            raise
        except MailNotFound:
            return self._fail(decision_id, GONE_MSG)
        except MailApiError as e:
            return self._fail(decision_id, str(e))
        with self.sf() as db:
            d = db.get(RuleDecision, decision_id)
            if removed:
                d.changes = {**(d.changes or {}), "removed": True}  # no Undo (spec §4)
            row = db.get(MailMessage, d.message_id)
            set_flags(row, new_labels)
            if planned.trash:
                row.state, row.offload_id = MailState.OFFLOADED, None  # in no offload batch
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
            if changes.get("removed"):
                d.reason = NOT_UNDOABLE_MSG
                db.commit()
                return UndoResult(DecisionStatus.APPLIED, [])
            # An offload is undone while it's still offloaded; a label change while in Gmail.
            if state != (MailState.OFFLOADED if changes.get("trash") else MailState.IN_MAIL):
                d.reason = NOT_UNDONE_MSG
                db.commit()
                return UndoResult(DecisionStatus.APPLIED, [])
        adapter = self.adapter_for(acc)
        left: list[str] = []
        try:
            if changes.get("trash"):
                await adapter.untrash(pid)
                new_labels, new_state = seen, MailState.IN_MAIL
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
        except WAIT_ERRORS as e:
            with self.sf() as db:
                db.get(RuleDecision, decision_id).reason = _wait_reason(e)
                db.commit()
            raise
        except MailApiError as e:  # includes MailNotFound
            msg = GONE_MSG if isinstance(e, MailNotFound) else str(e)
            with self.sf() as db:
                db.get(RuleDecision, decision_id).reason = "Couldn't undo: " + msg
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

    async def offload_by_hand(self, account_id: int, message_id: int) -> RuleDecision:
        """Offload on one mail open in the reader (mail panel spec §8): the path a rule's
        Offload takes, logged as the owner's own so it can be undone."""
        with self.sf() as db:
            row = db.get(MailMessage, message_id)
            if row is None or row.account_id != account_id:
                raise LookupError("That mail isn't here.")
            d = RuleDecision(
                account_id=account_id,
                message_id=message_id,
                how=DecisionHow.MANUAL,
                why="Offloaded by you.",
                actions=[{"kind": "offload"}],
                labels_seen=list(row.labels or []),
                status=DecisionStatus.SUGGESTED,
            )
            db.add(d)
            db.commit()
            did = d.id
        await self.apply(did)
        with self.sf() as db:
            return db.get(RuleDecision, did)
