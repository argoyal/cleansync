"""Google API units used per UTC day

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_usage",
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("bucket", sa.String(length=16), nullable=False),
        sa.Column("units", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("day", "bucket"),
    )


def downgrade() -> None:
    op.drop_table("api_usage")
