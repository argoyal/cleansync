"""Model limits: when a connection's daily limit resets, and its saved pause

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("ai_connections") as b:
        b.add_column(sa.Column("resets_at", sa.String(5), nullable=False, server_default="00:00"))
        b.add_column(sa.Column("resets_tz", sa.Text(), nullable=False, server_default="UTC"))
        b.add_column(sa.Column("paused_until", TZ, nullable=True))
        b.add_column(sa.Column("pause_kind", sa.String(16), nullable=True))
        b.add_column(sa.Column("pause_reason", sa.Text(), nullable=True))
        b.add_column(sa.Column("pause_detail", sa.Text(), nullable=True))
        b.add_column(sa.Column("paused_at", TZ, nullable=True))
        b.add_column(sa.Column("down_streak", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("ai_connections") as b:
        for col in (
            "down_streak",
            "paused_at",
            "pause_detail",
            "pause_reason",
            "pause_kind",
            "paused_until",
            "resets_tz",
            "resets_at",
        ):
            b.drop_column(col)
