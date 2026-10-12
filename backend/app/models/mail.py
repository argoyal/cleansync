from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.models.base import Base
from app.models.enums import JobState, MailItemResult, MailOffloadState, MailPhase, MailState


class MailMessage(Base):
    """One mail kept on this server (spec §4.1). Its .eml is at mail/<email>/<file_path>."""

    __tablename__ = "mail_messages"
    __table_args__ = (
        UniqueConstraint("account_id", "provider_id"),
        # The list walks mails newest first; the counts, totals and status read sizes and
        # states. Both are covering, so none of them reads the rows (issue #16).
        Index("ix_mail_messages_account_date_thread", "account_id", "date", "thread_id"),
        Index("ix_mail_messages_account_thread", "account_id", "thread_id"),
        Index(
            "ix_mail_messages_account_state_cover",
            "account_id",
            "state",
            "date",
            "thread_id",
            "size",
            "file_size",
        ),
        # Ids are the search table's row ids; never reuse one.
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    provider_id: Mapped[str] = mapped_column(String(64))  # Gmail's id (a new one after an import)
    thread_id: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # the Message-ID header
    from_addr: Mapped[str] = mapped_column(Text, default="")
    to_addrs: Mapped[str] = mapped_column(Text, default="")
    cc_addrs: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # Gmail's internal date
    size: Mapped[int] = mapped_column(BigInteger)  # Gmail's size estimate at download
    file_size: Mapped[int] = mapped_column(BigInteger)  # bytes of the .eml
    sha256: Mapped[str] = mapped_column(String(64))
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    unread: Mapped[bool] = mapped_column(Boolean, default=False)
    starred: Mapped[bool] = mapped_column(Boolean, default=False)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    attachments: Mapped[list[dict]] = mapped_column(JSON, default=list)  # name, type, size
    snippet: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(24), default=MailState.IN_MAIL)
    # Plan 4's mail_offloads row; a plain number (SQLite can't add the link to an old table).
    offload_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set just before a restore sends this mail to Gmail, cleared once the outcome is known.
    # While set, a new Gmail mail with the same Message-ID is that import (its answer got
    # lost), never a mail of its own.
    import_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MailMessageLabel(Base):
    """One label of one mail, with the columns the list and the counts read (issue #16).
    SQLite triggers on mail_messages fill it and keep it up to date (migration 0008); never
    write it from Python."""

    __tablename__ = "mail_message_labels"
    __table_args__ = (
        Index(
            "ix_mail_message_labels_lookup", "account_id", "label_id", "date", "thread_id", "size"
        ),
    )
    message_id: Mapped[int] = mapped_column(
        ForeignKey("mail_messages.id", ondelete="CASCADE"), primary_key=True
    )
    label_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    thread_id: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)


class MailLabel(Base):
    __tablename__ = "mail_labels"
    __table_args__ = (UniqueConstraint("account_id", "provider_label_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    provider_label_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(8))  # system | user


class MailSyncState(Base):
    """Where an account's mail backup is (spec §4.1), so it resumes after any stop."""

    __tablename__ = "mail_sync_state"
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    phase: Mapped[str] = mapped_column(String(16), default=MailPhase.INITIAL)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)  # Gmail's page token
    history_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    done: Mapped[int] = mapped_column(Integer, default=0)  # mails kept here
    total: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Gmail's count
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class MailFailure(Base):
    __tablename__ = "mail_failures"
    __table_args__ = (UniqueConstraint("account_id", "provider_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    provider_id: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    next_try_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MailJob(Base):
    """One mail job (backup, check, re-check; Plan 4: offload, empty trash, restore)."""

    __tablename__ = "mail_jobs"
    # Ids name log files; never reuse one.
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    trigger: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), default=JobState.RUNNING)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class MailRecheckSeen(Base):
    """Ids a re-check has seen so far, so it can resume (cleared when it finishes)."""

    __tablename__ = "mail_recheck_seen"
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    provider_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class MailOffload(Base):
    """One offload (spec 5.3): a filter or a picked list, run in batches."""

    __tablename__ = "mail_offloads"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    filter_text: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default=MailOffloadState.WAITING)
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    bytes: Mapped[int] = mapped_column(BigInteger, default=0)  # Gmail's sizes of what left
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MailOffloadItem(Base):
    __tablename__ = "mail_offload_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offload_id: Mapped[int] = mapped_column(
        ForeignKey("mail_offloads.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[int] = mapped_column(
        ForeignKey("mail_messages.id", ondelete="CASCADE"), index=True
    )
    result: Mapped[str] = mapped_column(String(16), default=MailItemResult.PENDING)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
