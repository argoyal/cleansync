"""One state for mail removed from Gmail (Offloaded), and the rule action Offload

Revision ID: 0011
Revises: 0010
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


# Fixed statements, one pair per table (no SQL is built from strings).
_ACTION_LISTS = (
    (
        sa.text("SELECT id, actions FROM rules"),
        sa.text("UPDATE rules SET actions = :v WHERE id = :i"),
    ),
    (
        sa.text("SELECT id, actions FROM rule_decisions"),
        sa.text("UPDATE rule_decisions SET actions = :v WHERE id = :i"),
    ),
)


def _load(raw):
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None


def _renamed(actions, old: str, new: str) -> list | None:
    """The list with each action of kind `old` renamed; None when nothing changed."""
    if not isinstance(actions, list):
        return None
    out = [
        {**a, "kind": new} if isinstance(a, dict) and a.get("kind") == old else a for a in actions
    ]
    return out if out != actions else None


def _rename_actions(conn, old: str, new: str) -> None:
    """The action kind `old` becomes `new` in rules, their saved versions and decisions."""
    for select_, update in _ACTION_LISTS:
        for rid, raw in conn.execute(select_).all():
            out = _renamed(_load(raw), old, new)
            if out is not None:
                conn.execute(update, {"v": json.dumps(out), "i": rid})
    for rid, raw in conn.execute(sa.text("SELECT id, snapshot FROM rule_versions")).all():
        snap = _load(raw)
        out = _renamed(snap.get("actions"), old, new) if isinstance(snap, dict) else None
        if out is not None:
            conn.execute(
                sa.text("UPDATE rule_versions SET snapshot = :v WHERE id = :i"),
                {"v": json.dumps({**snap, "actions": out}), "i": rid},
            )


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
