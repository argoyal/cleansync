"""mail offloads and the mails in each

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_offloads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("filter_text", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_mail_offloads_account_id", "mail_offloads", ["account_id"])
    op.create_table(
        "mail_offload_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("offload_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["offload_id"], ["mail_offloads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["mail_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mail_offload_items_offload_id", "mail_offload_items", ["offload_id"])
    op.create_index("ix_mail_offload_items_message_id", "mail_offload_items", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_mail_offload_items_message_id", table_name="mail_offload_items")
    op.drop_index("ix_mail_offload_items_offload_id", table_name="mail_offload_items")
    op.drop_table("mail_offload_items")
    op.drop_index("ix_mail_offloads_account_id", table_name="mail_offloads")
    op.drop_table("mail_offloads")
