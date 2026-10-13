"""One state for mail removed from Gmail (Offloaded), and the rule action Offload

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _rename_actions(conn, old: str, new: str) -> None:
    """Task 3 fills this in: the action kind `old` becomes `new` in rules, their versions
    and decisions."""


def upgrade() -> None:
    conn = op.get_bind()
    # The Local archive was "removed from Gmail, copy kept": that is Offloaded now. These
    # mails belong to no offload batch, so offload_id stays empty.
    conn.execute(
        sa.text("UPDATE mail_messages SET state = 'offloaded' WHERE state = 'local_archive'")
    )
    _rename_actions(conn, "delete", "offload")


def downgrade() -> None:
    # Mail stays offloaded: which rows were in the Local archive isn't known any more.
    _rename_actions(op.get_bind(), "offload", "delete")
