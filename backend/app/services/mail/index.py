"""Mail rows in the database (spec §4.1), the search table that goes with them, the backup
state, and failed downloads."""

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.clock import as_utc
from app.models import MailFailure, MailLabel, MailMessage, MailRecheckSeen, MailSyncState
from app.models.enums import MailPhase, MailState
from app.services.mail.adapter import MailMeta, ProviderLabel
from app.services.mail.store import ParsedMail

CHUNK = 500
FAIL_SHOWN_AFTER = 3


def _chunks(items: list, size: int = CHUNK):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def stored_ids(db: Session, account_id: int, ids: Iterable[str]) -> set[str]:
    found: set[str] = set()
    for chunk in _chunks(list(ids)):
        found |= set(
            db.scalars(
                select(MailMessage.provider_id).where(
                    MailMessage.account_id == account_id, MailMessage.provider_id.in_(chunk)
                )
            )
        )
    return found


def count_messages(db: Session, account_id: int) -> int:
    q = select(func.count()).select_from(MailMessage).where(MailMessage.account_id == account_id)
    return db.scalar(q) or 0


def stored_bytes(db: Session, account_id: int) -> int:
    q = select(func.sum(MailMessage.file_size)).where(MailMessage.account_id == account_id)
    return int(db.scalar(q) or 0)


def by_provider_id(db: Session, account_id: int, provider_id: str) -> MailMessage | None:
    return db.scalar(
        select(MailMessage).where(
            MailMessage.account_id == account_id, MailMessage.provider_id == provider_id
        )
    )


def set_flags(row: MailMessage, labels: Iterable[str]) -> None:
    labels = list(labels)
    row.labels = labels
    row.unread = "UNREAD" in labels
    row.starred = "STARRED" in labels


def add_message(
    db: Session,
    account_id: int,
    meta: MailMeta,
    parsed: ParsedMail,
    rel: str,
    file_size: int,
    sha256: str,
    now: datetime,
) -> MailMessage:
    """Called only after the .eml is complete on disk (spec §4.3)."""
    row = MailMessage(
        account_id=account_id,
        provider_id=meta.id,
        thread_id=meta.thread_id,
        message_id=parsed.message_id,
        from_addr=parsed.from_addr,
        to_addrs=parsed.to_addrs,
        cc_addrs=parsed.cc_addrs,
        subject=parsed.subject,
        date=meta.internal_date,
        size=meta.size,
        file_size=file_size,
        sha256=sha256,
        has_attachments=bool(parsed.attachments),
        attachments=parsed.attachments,
        snippet=meta.snippet[:300],
        file_path=rel,
        state=MailState.IN_MAIL,
        first_seen_at=now,
        updated_at=now,
    )
    set_flags(row, meta.labels)
    db.add(row)
    db.flush()
    db.execute(
        text(
            "INSERT INTO mail_fts(rowid, subject, from_addr, to_addrs, body) "
            "VALUES (:id, :subject, :from_addr, :to_addrs, :body)"
        ),
        {
            "id": row.id,
            "subject": parsed.subject,
            "from_addr": parsed.from_addr,
            "to_addrs": f"{parsed.to_addrs} {parsed.cc_addrs}".strip(),
            "body": parsed.body_text,
        },
    )
    db.commit()
    return row


def delete_search_rows(db: Session, ids: Iterable[int]) -> None:
    for chunk in _chunks(list(ids)):
        db.execute(text("DELETE FROM mail_fts WHERE rowid = :id"), [{"id": i} for i in chunk])
    db.commit()


def sync_state(db: Session, account_id: int) -> MailSyncState:
    st = db.get(MailSyncState, account_id)
    if st is None:
        st = MailSyncState(account_id=account_id, phase=MailPhase.INITIAL, done=0)
        db.add(st)
        db.commit()
    return st


def save_labels(db: Session, account_id: int, labels: list[ProviderLabel]) -> None:
    """The account's labels as Gmail has them now; labels deleted in Gmail leave the list."""
    have = {
        row.provider_label_id: row
        for row in db.scalars(select(MailLabel).where(MailLabel.account_id == account_id))
    }
    keep = set()
    for lab in labels:
        keep.add(lab.id)
        row = have.get(lab.id)
        if row is None:
            db.add(
                MailLabel(
                    account_id=account_id, provider_label_id=lab.id, name=lab.name, type=lab.type
                )
            )
        else:
            row.name, row.type = lab.name, lab.type
    for lid, row in have.items():
        if lid not in keep:
            db.delete(row)
    db.commit()


def retry_after(attempts: int) -> timedelta:
    """5 minutes after the first failure, then 10, 20 … at most once a day."""
    return timedelta(minutes=min(5 * 2 ** (max(attempts, 1) - 1), 24 * 60))


def _failure(db: Session, account_id: int, provider_id: str) -> MailFailure | None:
    return db.scalar(
        select(MailFailure).where(
            MailFailure.account_id == account_id, MailFailure.provider_id == provider_id
        )
    )


def record_failure(
    db: Session, account_id: int, provider_id: str, error: str, now: datetime
) -> MailFailure:
    f = _failure(db, account_id, provider_id)
    if f is None:
        f = MailFailure(account_id=account_id, provider_id=provider_id, attempts=0)
        db.add(f)
    f.attempts += 1
    f.last_error = error[:500]
    f.next_try_at = now + retry_after(f.attempts)
    db.commit()
    return f


def clear_failure(db: Session, account_id: int, provider_id: str) -> None:
    db.execute(
        delete(MailFailure).where(
            MailFailure.account_id == account_id, MailFailure.provider_id == provider_id
        )
    )
    db.commit()


def waiting_failures(db: Session, account_id: int, ids: Iterable[str], now: datetime) -> set[str]:
    """Of these ids, the ones that failed and are not due for another try yet."""
    out: set[str] = set()
    for chunk in _chunks(list(ids)):
        rows = db.execute(
            select(MailFailure.provider_id, MailFailure.next_try_at).where(
                MailFailure.account_id == account_id, MailFailure.provider_id.in_(chunk)
            )
        ).all()
        out |= {pid for pid, at in rows if as_utc(at) > now}
    return out


def due_failures(db: Session, account_id: int, now: datetime, limit: int = 200) -> list[str]:
    rows = db.execute(
        select(MailFailure.provider_id, MailFailure.next_try_at)
        .where(MailFailure.account_id == account_id)
        .order_by(MailFailure.next_try_at)
    ).all()
    return [pid for pid, at in rows if as_utc(at) <= now][:limit]


def shown_failures(db: Session, account_id: int) -> list[MailFailure]:
    return list(
        db.scalars(
            select(MailFailure)
            .where(MailFailure.account_id == account_id, MailFailure.attempts >= FAIL_SHOWN_AFTER)
            .order_by(MailFailure.provider_id)
        )
    )


GONE_LABELS = frozenset({"TRASH", "SPAM"})


def apply_labels(
    db: Session, account_id: int, provider_id: str, labels: Iterable[str]
) -> MailMessage | None:
    """Gmail says these are the mail's labels now (spec §4.4)."""
    row = by_provider_id(db, account_id, provider_id)
    if row is None:
        return None
    labels = list(labels)
    gone = bool(GONE_LABELS & set(labels))
    if row.state == MailState.OFFLOADED:
        if "TRASH" in labels:
            return row  # in the trash because of CleanSync's own offload: no change (spec §4.4)
        if gone:
            # Moved from Trash to Spam by the owner: Deleted in Gmail, and it stays on its
            # offload's page.
            row.state = MailState.DELETED
            db.commit()
            return row
    if gone:
        # Moved to Trash or Spam by the owner: Deleted in Gmail. The labels it had are kept,
        # so a restore by import can put them back.
        if row.state == MailState.IN_MAIL:
            row.state = MailState.DELETED
    else:
        # Back out of Trash or Spam (an offloaded mail too: it is in Gmail again), or a plain
        # label change.
        row.state, row.offload_id = MailState.IN_MAIL, None
        set_flags(row, labels)
    db.commit()
    return row


def mark_deleted(db: Session, account_id: int, provider_id: str) -> MailMessage | None:
    """Deleted for good in Gmail. The file stays (spec §4.4); an offloaded mail leaving
    Gmail's trash is Gmail emptying its trash and stays offloaded."""
    row = by_provider_id(db, account_id, provider_id)
    if row is not None and row.state == MailState.IN_MAIL:
        row.state = MailState.DELETED
        db.commit()
    return row


def remember_seen(db: Session, account_id: int, ids: Iterable[str]) -> None:
    ids = list(ids)
    have = set(
        db.scalars(
            select(MailRecheckSeen.provider_id).where(
                MailRecheckSeen.account_id == account_id, MailRecheckSeen.provider_id.in_(ids)
            )
        )
    )
    for pid in ids:
        if pid not in have:
            db.add(MailRecheckSeen(account_id=account_id, provider_id=pid))
    db.commit()


def mark_unseen_deleted(db: Session, account_id: int) -> int:
    """After a complete re-check listing: mail kept as 'in mail' that Gmail no longer lists."""
    result = db.execute(
        text(
            "UPDATE mail_messages SET state = :deleted WHERE account_id = :a AND state = :in_mail "
            "AND provider_id NOT IN "
            "(SELECT provider_id FROM mail_recheck_seen WHERE account_id = :a)"
        ),
        {"a": account_id, "deleted": MailState.DELETED.value, "in_mail": MailState.IN_MAIL.value},
    )
    db.commit()
    return result.rowcount or 0


def mark_seen_back_in_mail(db: Session, account_id: int) -> int:
    """After a complete re-check listing: mail marked deleted, or offloaded, that Gmail lists
    again was taken back out of Trash or Spam by the owner, so it is in Gmail again (as
    keeping up does). The listing gives no labels, so the stored ones stay.
    Relies on offloads being refused while the mail phase is RECHECK or INITIAL (Plan 4)."""
    result = db.execute(
        text(
            "UPDATE mail_messages SET state = :in_mail, offload_id = NULL "
            "WHERE account_id = :a AND state IN (:deleted, :offloaded) "
            "AND provider_id IN "
            "(SELECT provider_id FROM mail_recheck_seen WHERE account_id = :a)"
        ),
        {
            "a": account_id,
            "in_mail": MailState.IN_MAIL.value,
            "deleted": MailState.DELETED.value,
            "offloaded": MailState.OFFLOADED.value,
        },
    )
    db.commit()
    return result.rowcount or 0


def clear_seen(db: Session, account_id: int) -> None:
    db.execute(delete(MailRecheckSeen).where(MailRecheckSeen.account_id == account_id))
    db.commit()
