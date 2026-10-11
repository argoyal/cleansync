"""mail: messages, labels, backup state, failures, jobs, re-check ids, full-text search

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _account_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE")


def upgrade() -> None:
    op.create_table(
        "mail_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("from_addr", sa.Text(), nullable=False),
        sa.Column("to_addrs", sa.Text(), nullable=False),
        sa.Column("cc_addrs", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("unread", sa.Boolean(), nullable=False),
        sa.Column("starred", sa.Boolean(), nullable=False),
        sa.Column("has_attachments", sa.Boolean(), nullable=False),
        sa.Column("attachments", sa.JSON(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("offload_id", sa.Integer(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _account_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "provider_id"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_mail_messages_account_date", "mail_messages", ["account_id", "date"])
    op.create_index("ix_mail_messages_account_thread", "mail_messages", ["account_id", "thread_id"])
    op.create_index("ix_mail_messages_account_state", "mail_messages", ["account_id", "state"])
    op.create_table(
        "mail_labels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("provider_label_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.String(length=8), nullable=False),
        _account_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "provider_label_id"),
    )
    op.create_table(
        "mail_sync_state",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("history_id", sa.String(length=32), nullable=True),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        _account_fk(),
        sa.PrimaryKeyConstraint("account_id"),
    )
    op.create_table(
        "mail_failures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("next_try_at", sa.DateTime(timezone=True), nullable=False),
        _account_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "provider_id"),
    )
    op.create_table(
        "mail_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("log_path", sa.String(length=1024), nullable=True),
        _account_fk(),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_mail_jobs_account_id", "mail_jobs", ["account_id"])
    op.create_table(
        "mail_recheck_seen",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        _account_fk(),
        sa.PrimaryKeyConstraint("account_id", "provider_id"),
    )
    # rowid = mail_messages.id. The body is the mail's text (HTML stripped), cut at 200,000
    # characters; it is only for search.
    op.execute(
        "CREATE VIRTUAL TABLE mail_fts USING fts5("
        "subject, from_addr, to_addrs, body, tokenize = 'unicode61 remove_diacritics 2')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mail_fts")
    op.drop_table("mail_recheck_seen")
    op.drop_index("ix_mail_jobs_account_id", table_name="mail_jobs")
    op.drop_table("mail_jobs")
    op.drop_table("mail_failures")
    op.drop_table("mail_sync_state")
    op.drop_table("mail_labels")
    op.drop_index("ix_mail_messages_account_state", table_name="mail_messages")
    op.drop_index("ix_mail_messages_account_thread", table_name="mail_messages")
    op.drop_index("ix_mail_messages_account_date", table_name="mail_messages")
    op.drop_table("mail_messages")
