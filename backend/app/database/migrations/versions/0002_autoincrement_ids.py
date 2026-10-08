"""never reuse sync pair and job ids

A deleted pair's id (and its jobs' ids) must not be handed to a new pair: files on disk are
named after them (bisync workdir, filters, previews, job logs).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_TABLES = ("sync_pairs", "sync_jobs")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(
            table, recreate="always", table_kwargs={"sqlite_autoincrement": True}
        ):
            pass


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(
            table, recreate="always", table_kwargs={"sqlite_autoincrement": False}
        ):
            pass
