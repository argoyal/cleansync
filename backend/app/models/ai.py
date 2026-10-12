from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.models.base import Base


class AiConnection(Base):
    """One OpenAI-style endpoint (AI rules spec §4). The key and header values are files in
    app/data/secrets/ai/, never columns."""

    __tablename__ = "ai_connections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(Text)
    has_key: Mapped[bool] = mapped_column(Boolean, default=False)
    header_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    # model name -> the answer mode that worked last ("json_schema" | "json_object" | "text")
    modes: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AiTask(Base):
    __tablename__ = "ai_tasks"
    task: Mapped[str] = mapped_column(String(32), primary_key=True)
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_connections.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(Text, default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.0)
    timeout_s: Mapped[int] = mapped_column(Integer, default=60)
