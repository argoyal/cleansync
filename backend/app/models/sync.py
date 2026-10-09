from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.models.base import Base
from app.models.enums import ConflictState, Direction, JobState, OffloadState, PairStatus


class SyncPair(Base):
    __tablename__ = "sync_pairs"
    # Ids name files on disk (workdir, filters, previews); a deleted pair's id is never reused.
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    local_subpath: Mapped[str] = mapped_column(String(1024), default="")
    remote_path: Mapped[str] = mapped_column(String(1024), default="")
    direction: Mapped[str] = mapped_column(String(24), default=Direction.BIDIRECTIONAL)
    schedule_interval_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    max_deletions: Mapped[int] = mapped_column(Integer, default=10)
    backups_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    backup_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initialized: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_resync: Mapped[bool] = mapped_column(Boolean, default=False)
    override_next: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default=PairStatus.NEW)
    attention_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    # Ids name job log files; a deleted job's id is never reused.
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pair_id: Mapped[int] = mapped_column(
        ForeignKey("sync_pairs.id", ondelete="CASCADE"), index=True
    )
    trigger: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), default=JobState.QUEUED)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    files_uploaded: Mapped[int] = mapped_column(Integer, default=0)
    files_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    files_deleted_local: Mapped[int] = mapped_column(Integer, default=0)
    files_deleted_remote: Mapped[int] = mapped_column(Integer, default=0)
    files_changed: Mapped[int] = mapped_column(Integer, default=0)
    conflicts: Mapped[int] = mapped_column(Integer, default=0)
    planned_deletions: Mapped[int] = mapped_column(Integer, default=0)
    bytes_transferred: Mapped[int] = mapped_column(BigInteger, default=0)
    override_used: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class PlannedChange(Base):
    __tablename__ = "planned_changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("sync_jobs.id", ondelete="CASCADE"), index=True)
    side: Mapped[str] = mapped_column(String(8))  # "local" | "remote"
    action: Mapped[str] = mapped_column(String(16))  # "delete" | "copy"
    path: Mapped[str] = mapped_column(Text)


class Conflict(Base):
    __tablename__ = "conflicts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pair_id: Mapped[int] = mapped_column(
        ForeignKey("sync_pairs.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(Text)
    local_variant: Mapped[str] = mapped_column(Text)
    remote_variant: Mapped[str] = mapped_column(Text)
    local_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    local_mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remote_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    remote_mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default=ConflictState.OPEN)
    resolution: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Offload(Base):
    __tablename__ = "offloads"
    # Ids name files on disk (app/data/offloads/<id>.json); never reuse one.
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pair_id: Mapped[int] = mapped_column(
        ForeignKey("sync_pairs.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(Text)  # pair path, '/'-separated
    is_dir: Mapped[bool] = mapped_column(Boolean, default=False)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    state: Mapped[str] = mapped_column(String(16), default=OffloadState.PENDING)
    trashed_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    trash_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    trash_emptied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
