from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApiUsage(Base):
    """Google API units used on one UTC day (spec §7): counted calls, and rclone's estimate."""

    __tablename__ = "api_usage"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD, UTC
    bucket: Mapped[str] = mapped_column(String(16), primary_key=True)  # counted | drive_estimate
    units: Mapped[int] = mapped_column(BigInteger, default=0)
