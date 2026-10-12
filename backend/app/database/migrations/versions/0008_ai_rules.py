"""AI rules: model connections and tasks, rules and their versions, remembered senders,
the sorting to-do list, decisions, rule jobs, corrections, per-mailbox sorting state

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "ai_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("has_key", sa.Boolean(), nullable=False),
        sa.Column("header_names", sa.JSON(), nullable=False),
        sa.Column("modes", sa.JSON(), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ai_tasks",
        sa.Column("task", sa.String(32), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("timeout_s", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["ai_connections.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("task"),
    )
    op.create_table(
        "rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("combine", sa.String(16), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("updated_at", TZ, nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rules_account_id", "rules", ["account_id"])
    op.create_table(
        "rule_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("saved_by", sa.String(64), nullable=False),
        sa.Column("saved_at", TZ, nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "version"),
    )
    op.create_index("ix_rule_versions_rule_id", "rule_versions", ["rule_id"])
    op.create_table(
        "rule_senders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("approvals", sa.Integer(), nullable=False),
        sa.Column("learned_at", TZ, nullable=True),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "sender"),
    )
    op.create_index("ix_rule_senders_rule_id", "rule_senders", ["rule_id"])
    op.create_index("ix_rule_senders_account_id", "rule_senders", ["account_id"])
    op.create_table(
        "rule_todo",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("added_at", TZ, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_try_at", TZ, nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["mail_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "message_id"),
    )
    op.create_index("ix_rule_todo_due", "rule_todo", ["account_id", "next_try_at"])
    op.create_table(
        "rule_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column("rule_name", sa.Text(), nullable=False),
        sa.Column("how", sa.String(12), nullable=False),
        sa.Column("why", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("took_ms", sa.Integer(), nullable=False),
        sa.Column("raw_answer", sa.Text(), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("labels_seen", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("moved_back_at", TZ, nullable=True),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("applied_at", TZ, nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["mail_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_rule_decisions_account_status", "rule_decisions", ["account_id", "status"])
    op.create_index("ix_rule_decisions_message", "rule_decisions", ["message_id"])
    op.create_index("ix_rule_decisions_job_id", "rule_decisions", ["job_id"])
    op.create_table(
        "rule_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("filter_text", sa.Text(), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("decided", sa.Integer(), nullable=False),
        sa.Column("applied", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("finished_at", TZ, nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_rule_jobs_account_id", "rule_jobs", ["account_id"])
    op.create_table(
        "rule_corrections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("from_rule", sa.Text(), nullable=False),
        sa.Column("to_rule", sa.Text(), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rule_corrections_sender", "rule_corrections", ["account_id", "sender"])
    op.create_table(
        "rule_accounts",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("sorting_enabled", sa.Boolean(), nullable=False),
        sa.Column("failing_since", TZ, nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_ok_at", TZ, nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
    )


def downgrade() -> None:
    op.drop_table("rule_accounts")
    op.drop_index("ix_rule_corrections_sender", table_name="rule_corrections")
    op.drop_table("rule_corrections")
    op.drop_index("ix_rule_jobs_account_id", table_name="rule_jobs")
    op.drop_table("rule_jobs")
    op.drop_index("ix_rule_decisions_job_id", table_name="rule_decisions")
    op.drop_index("ix_rule_decisions_message", table_name="rule_decisions")
    op.drop_index("ix_rule_decisions_account_status", table_name="rule_decisions")
    op.drop_table("rule_decisions")
    op.drop_index("ix_rule_todo_due", table_name="rule_todo")
    op.drop_table("rule_todo")
    op.drop_index("ix_rule_senders_account_id", table_name="rule_senders")
    op.drop_index("ix_rule_senders_rule_id", table_name="rule_senders")
    op.drop_table("rule_senders")
    op.drop_index("ix_rule_versions_rule_id", table_name="rule_versions")
    op.drop_table("rule_versions")
    op.drop_index("ix_rules_account_id", table_name="rules")
    op.drop_table("rules")
    op.drop_table("ai_tasks")
    op.drop_table("ai_connections")
