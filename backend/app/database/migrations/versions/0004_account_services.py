"""accounts: provider, granted permissions, and one row per account and service

Existing accounts keep syncing: Files is active, Mail is not set up, and the only permission
they are known to have is Drive.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def upgrade() -> None:
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("provider", sa.String(length=16), nullable=False, server_default="google")
        )
        batch_op.add_column(sa.Column("scopes", sa.Text(), nullable=False, server_default=""))
    op.execute(sa.text("UPDATE accounts SET scopes = :s").bindparams(s=DRIVE_SCOPE))
    op.create_table(
        "account_services",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "kind"),
    )
    op.execute(
        "INSERT INTO account_services (account_id, kind, state, updated_at) "
        "SELECT id, 'files', 'active', CURRENT_TIMESTAMP FROM accounts"
    )
    op.execute(
        "INSERT INTO account_services (account_id, kind, state, updated_at) "
        "SELECT id, 'mail', 'not_set_up', CURRENT_TIMESTAMP FROM accounts"
    )


def downgrade() -> None:
    op.drop_table("account_services")
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_column("scopes")
        batch_op.drop_column("provider")
