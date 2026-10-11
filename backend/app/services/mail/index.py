"""Mail rows in the database (spec §4.1), the search table that goes with them, the backup
state, and failed downloads."""

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.clock import as_utc
from app.models import MailFailure, MailLabel, MailMessage, MailSyncState
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
