"""offloads

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "offloads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pair_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("is_dir", sa.Boolean(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("trashed_ids", sa.JSON(), nullable=False),
        sa.Column("trash_verified", sa.Boolean(), nullable=False),
        sa.Column("trash_emptied", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["pair_id"], ["sync_pairs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table("offloads", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_offloads_pair_id"), ["pair_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("offloads", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_offloads_pair_id"))
    op.drop_table("offloads")
