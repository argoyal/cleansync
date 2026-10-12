"""mail speed (issue #16): covering indexes, and a label table the triggers keep up to date

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# One row per label of the new or changed mail. Labels that aren't valid JSON give no rows
# instead of failing the save (or this upgrade).
_FILL_NEW = (
    "INSERT OR IGNORE INTO mail_message_labels (message_id, account_id, label_id) "
    "SELECT NEW.id, NEW.account_id, j.value FROM json_each("
    "CASE WHEN json_valid(NEW.labels) THEN NEW.labels ELSE '[]' END) j WHERE j.type = 'text'"
)
# The same for every mail already stored (run once, at upgrade).
_FILL_ALL = (
    "INSERT OR IGNORE INTO mail_message_labels (message_id, account_id, label_id) "
    "SELECT m.id, m.account_id, j.value FROM mail_messages m, json_each("
    "CASE WHEN json_valid(m.labels) THEN m.labels ELSE '[]' END) j WHERE j.type = 'text'"
)


def upgrade() -> None:
    op.create_table(
        "mail_message_labels",
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("label_id", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["mail_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("message_id", "label_id"),
    )
    op.create_index(
        "ix_mail_message_labels_lookup",
        "mail_message_labels",
        ["account_id", "label_id"],
    )
    op.drop_index("ix_mail_messages_account_date", table_name="mail_messages")
    op.drop_index("ix_mail_messages_account_state", table_name="mail_messages")
    op.create_index(
        "ix_mail_messages_account_date_thread", "mail_messages", ["account_id", "date", "thread_id"]
    )
    op.create_index(
        "ix_mail_messages_account_state_cover",
        "mail_messages",
        ["account_id", "state", "date", "thread_id", "size", "file_size"],
    )
    # Every writer of mail_messages (the ORM and raw SQL alike) keeps the label table right, so
    # no code path can forget it. Deletes also cascade, but the migration runner turns foreign
    # keys off, so the delete trigger covers that case.
    op.execute(
        f"CREATE TRIGGER mail_labels_ai AFTER INSERT ON mail_messages BEGIN {_FILL_NEW}; END"
    )
    op.execute(  # fixed text, no input
        "CREATE TRIGGER mail_labels_au AFTER UPDATE OF labels ON mail_messages BEGIN "  # noqa: S608
        f"DELETE FROM mail_message_labels WHERE message_id = OLD.id; {_FILL_NEW}; END"
    )
    op.execute(
        "CREATE TRIGGER mail_labels_ad AFTER DELETE ON mail_messages BEGIN "
        "DELETE FROM mail_message_labels WHERE message_id = OLD.id; END"
    )
    op.execute(_FILL_ALL)


def downgrade() -> None:
    for name in ("mail_labels_ad", "mail_labels_au", "mail_labels_ai"):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_index("ix_mail_messages_account_state_cover", table_name="mail_messages")
    op.drop_index("ix_mail_messages_account_date_thread", table_name="mail_messages")
    op.create_index("ix_mail_messages_account_state", "mail_messages", ["account_id", "state"])
    op.create_index("ix_mail_messages_account_date", "mail_messages", ["account_id", "date"])
    op.drop_index("ix_mail_message_labels_lookup", table_name="mail_message_labels")
    op.drop_table("mail_message_labels")
