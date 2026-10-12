from datetime import datetime

from sqlalchemy import (
    JSON,
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
from app.models.enums import DecisionHow, DecisionStatus, RuleCombine, RuleJobState, RuleMode


class Rule(Base):
    """One mailbox's rule (AI rules spec §5). `conditions` holds the fixed conditions as
    written by match.Conditions.to_json; `actions` is a list of {"kind", "label"?}."""

    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    combine: Mapped[str] = mapped_column(String(16), default=RuleCombine.AI_ONLY)
    actions: Mapped[list[dict]] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(12), default=RuleMode.SUGGEST)
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RuleVersion(Base):
    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(
        JSON
    )  # name, description, conditions, combine, actions, mode
    saved_by: Mapped[str] = mapped_column(String(64), default="")
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RuleSender(Base):
    """A sender approved into a rule; remembered once `approvals` reaches 3 (spec §5)."""

    __tablename__ = "rule_senders"
    __table_args__ = (UniqueConstraint("rule_id", "sender"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    sender: Mapped[str] = mapped_column(Text)  # lower-case address
    approvals: Mapped[int] = mapped_column(Integer, default=0)
    learned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RuleTodo(Base):
    """A mail waiting to be decided: new inbox mail, or a rule job's mail (job_id set)."""

    __tablename__ = "rule_todo"
    __table_args__ = (Index("ix_rule_todo_due", "account_id", "next_try_at"),)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    message_id: Mapped[int] = mapped_column(
        ForeignKey("mail_messages.id", ondelete="CASCADE"), primary_key=True
    )
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_try_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class RuleDecision(Base):
    __tablename__ = "rule_decisions"
    __table_args__ = (
        Index("ix_rule_decisions_account_status", "account_id", "status"),
        Index("ix_rule_decisions_message", "message_id"),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    message_id: Mapped[int] = mapped_column(ForeignKey("mail_messages.id", ondelete="CASCADE"))
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("rules.id", ondelete="SET NULL"), nullable=True
    )
    rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_name: Mapped[str] = mapped_column(Text, default="")  # kept for when the rule is gone
    how: Mapped[str] = mapped_column(String(12), default=DecisionHow.NONE)
    why: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(Text, default="")
    took_ms: Mapped[int] = mapped_column(Integer, default=0)
    raw_answer: Mapped[str | None] = mapped_column(Text, nullable=True)  # only when unreadable
    actions: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # Exactly what was (or will be) sent to Gmail: label ids to add/remove, and trash.
    changes: Mapped[dict] = mapped_column(JSON, default=dict)
    labels_seen: Mapped[list[str]] = mapped_column(JSON, default=list)  # labels when decided
    status: Mapped[str] = mapped_column(String(12), default=DecisionStatus.NO_RULE)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    moved_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RuleJob(Base):
    __tablename__ = "rule_jobs"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    filter_text: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(12), default=RuleJobState.PREVIEWING)
    total: Mapped[int] = mapped_column(Integer, default=0)
    decided: Mapped[int] = mapped_column(Integer, default=0)
    applied: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RuleCorrection(Base):
    __tablename__ = "rule_corrections"
    __table_args__ = (Index("ix_rule_corrections_sender", "account_id", "sender"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    sender: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text, default="")
    from_rule: Mapped[str] = mapped_column(Text, default="")  # names, so hints survive edits
    to_rule: Mapped[str] = mapped_column(Text, default="")  # "" = none
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RuleAccount(Base):
    """Per mailbox: is AI sorting on, and how it is going (shown in the mail status line)."""

    __tablename__ = "rule_accounts"
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    sorting_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    failing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
