"""A run of rules keeps its own rules, view and search

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Runs from before this keep [] in rule_ids, which means every enabled rule
    # (running rules spec §8).
    with op.batch_alter_table("rule_jobs", table_kwargs={"sqlite_autoincrement": True}) as b:
        b.add_column(sa.Column("rule_ids", sa.JSON(), nullable=False, server_default="[]"))
        b.add_column(sa.Column("view", sa.String(32), nullable=False, server_default=""))
        b.add_column(sa.Column("q", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("rule_jobs", table_kwargs={"sqlite_autoincrement": True}) as b:
        for col in ("q", "view", "rule_ids"):
            b.drop_column(col)
