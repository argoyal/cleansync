"""Permanent delete per mailbox, and Delete forever jobs beside offloads

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# mail_offloads keeps AUTOINCREMENT through the table rebuild, so ids are never reused.
_OFFLOADS = {"sqlite_autoincrement": True}


def upgrade() -> None:
    with op.batch_alter_table("accounts") as b:
        b.add_column(
            sa.Column("permanent_delete", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        b.add_column(sa.Column("permanent_delete_note", sa.Text(), nullable=True))
    with op.batch_alter_table("mail_offloads", table_kwargs=_OFFLOADS) as b:
        b.add_column(sa.Column("kind", sa.String(20), nullable=False, server_default="offload"))
        b.add_column(sa.Column("removed", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(
            sa.Column("stop_asked", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    # Older code would read a Delete forever job as an offload: those rows go.
    op.execute(sa.text("DELETE FROM mail_offloads WHERE kind = 'delete_forever'"))
    with op.batch_alter_table("mail_offloads", table_kwargs=_OFFLOADS) as b:
        for col in ("stop_asked", "removed", "kind"):
            b.drop_column(col)
    with op.batch_alter_table("accounts") as b:
        for col in ("permanent_delete_note", "permanent_delete"):
            b.drop_column(col)
