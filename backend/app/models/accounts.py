from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.models.base import Base
from app.models.enums import AccountStatus


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    remote_name: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=AccountStatus.CONNECTED)
    quota_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quota_used: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quota_trash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
