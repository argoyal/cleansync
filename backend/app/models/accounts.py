from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.models.base import Base
from app.models.enums import AccountStatus, Provider, ServiceState


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    remote_name: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=AccountStatus.CONNECTED)
    provider: Mapped[str] = mapped_column(
        String(16), default=Provider.GOOGLE, server_default="google"
    )
    # The permissions Google granted at the last sign-in, space-separated (its "scope" answer).
    scopes: Mapped[str] = mapped_column(Text, default="", server_default="")
    quota_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quota_used: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quota_trash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AccountService(Base):
    """One per account and service (Files, Mail): is it set up, and does it work."""

    __tablename__ = "account_services"
    __table_args__ = (UniqueConstraint("account_id", "kind"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(20), default=ServiceState.NOT_SET_UP)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
