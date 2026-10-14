"""Unsubscribe (0.5.2): saved unsubscribe headers and sender on each mail, sender statuses,
unsubscribe jobs; Allow Gmail filters and the profile photo on accounts.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN: a batch rebuild of mail_messages would drop its triggers (0008).
    op.add_column("mail_messages", sa.Column("list_unsubscribe", sa.Text(), nullable=True))
    op.add_column("mail_messages", sa.Column("list_unsubscribe_post", sa.Text(), nullable=True))
    # TEXT, not VARCHAR(320): SQLite sizes a table's rows by the declared lengths, and a wide
    # estimate makes it stop walking the date index (test_mail_speed_big guards this).
    op.add_column("mail_messages", sa.Column("sender", sa.Text(), nullable=True))
    op.add_column(
        "mail_messages",
        sa.Column("headers_scanned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_mail_unsub_sender",
        "mail_messages",
        ["account_id", "sender"],
        sqlite_where=sa.text("list_unsubscribe IS NOT NULL"),
    )
    op.create_index(
        "ix_mail_headers_todo",
        "mail_messages",
        ["account_id", "id"],
        sqlite_where=sa.text("headers_scanned = 0"),
    )
    with op.batch_alter_table("accounts") as b:
        b.add_column(
            sa.Column("gmail_filters", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        b.add_column(sa.Column("gmail_filters_note", sa.Text(), nullable=True))
        b.add_column(sa.Column("photo_type", sa.String(32), nullable=True))
        b.add_column(sa.Column("photo_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "unsubscribe_senders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("address", sa.String(320), nullable=False),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("filter_id", sa.String(128), nullable=True),
        sa.Column("method", sa.String(20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "address"),
    )
    op.create_table(
        "unsubscribe_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stop_asked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "unsubscribe_job_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("unsubscribe_jobs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("address", sa.String(320), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=True),  # NULL: not reached yet
        sa.Column("method", sa.String(20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),  # for Open page / Write to it
    )


def downgrade() -> None:
    op.drop_table("unsubscribe_job_items")
    op.drop_table("unsubscribe_jobs")
    op.drop_table("unsubscribe_senders")
    with op.batch_alter_table("accounts") as b:
        for col in ("photo_updated_at", "photo_type", "gmail_filters_note", "gmail_filters"):
            b.drop_column(col)
    op.drop_index("ix_mail_headers_todo", table_name="mail_messages")
    op.drop_index("ix_mail_unsub_sender", table_name="mail_messages")
    for col in ("headers_scanned", "sender", "list_unsubscribe_post", "list_unsubscribe"):
        op.drop_column("mail_messages", col)
