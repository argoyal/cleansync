from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.models.base import Base


class UnsubscribeSender(Base):
    """What the owner decided about one sender in one mailbox (0.5.2 §3)."""

    __tablename__ = "unsubscribe_senders"
    __table_args__ = (UniqueConstraint("account_id", "address"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    address: Mapped[str] = mapped_column(String(320))
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    filter_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class UnsubscribeJob(Base):
    __tablename__ = "unsubscribe_jobs"
    __table_args__ = ({"sqlite_autoincrement": True},)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20))
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    stop_asked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UnsubscribeJobItem(Base):
    __tablename__ = "unsubscribe_job_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("unsubscribe_jobs.id", ondelete="CASCADE"), index=True
    )
    address: Mapped[str] = mapped_column(String(320))
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NULL: not reached
    method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
