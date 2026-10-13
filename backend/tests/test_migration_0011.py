"""Migration 0011: one state for mail removed from Gmail (mail panel spec §8)."""

import json

from sqlalchemy import text

from app.database.engine import make_engine
from app.database.migrate import run_migrations
from tests.test_mail_speed_schema import _downgrade

STAMP = "2026-01-02 03:04:05.000000"


def old_database(tmp_path):
    """A 0.4.0 database: one mail in the Local archive, one in Gmail, one offloaded, and a
    rule, a rule version and a decision that use the old action name."""
    f = tmp_path / "v040.db"
    run_migrations(f, "0010")
    eng = make_engine(f)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO accounts (id, email, remote_name, status, connected_at) "
                "VALUES (1, 'a@x.com', 'a', 'CONNECTED', :t)"
            ),
            {"t": STAMP},
        )
        for i, state in ((1, "local_archive"), (2, "in_mail"), (3, "offloaded")):
            c.execute(
                text(
                    "INSERT INTO mail_messages (id, account_id, provider_id, thread_id, "
                    "from_addr, to_addrs, cc_addrs, subject, date, size, file_size, sha256, "
                    "labels, unread, starred, has_attachments, attachments, snippet, "
                    "file_path, state, first_seen_at, updated_at) VALUES (:i, 1, :p, :p, '', "
                    "'', '', '', :t, 10, 12, :sha, '[]', 0, 0, 0, '[]', '', 'x', :s, :t, :t)"
                ),
                {"i": i, "p": f"m{i}", "t": STAMP, "sha": "0" * 64, "s": state},
            )
        c.execute(
            text(
                "INSERT INTO rules (id, account_id, name, description, conditions, combine, "
                "actions, mode, position, enabled, version, created_at, updated_at) VALUES "
                "(1, 1, 'Junk', 'junk mail', '{}', 'ai_only', :a, 'suggest', 0, 1, 1, :t, :t)"
            ),
            {"a": json.dumps([{"kind": "delete"}]), "t": STAMP},
        )
        c.execute(
            text(
                "INSERT INTO rules (id, account_id, name, description, conditions, combine, "
                "actions, mode, position, enabled, version, created_at, updated_at) VALUES "
                "(2, 1, 'News', 'digests', '{}', 'ai_only', :a, 'suggest', 1, 1, 1, :t, :t)"
            ),
            {
                "a": json.dumps([{"kind": "label", "label": "delete"}, {"kind": "archive"}]),
                "t": STAMP,
            },
        )
        c.execute(
            text(
                "INSERT INTO rule_versions (id, rule_id, version, snapshot, saved_by, saved_at) "
                "VALUES (1, 1, 1, :s, 'admin', :t)"
            ),
            {"s": json.dumps({"name": "Junk", "actions": [{"kind": "delete"}]}), "t": STAMP},
        )
        c.execute(
            text(
                "INSERT INTO rule_decisions (id, account_id, message_id, rule_id, rule_version, "
                "rule_name, how, why, model, took_ms, actions, changes, labels_seen, status, "
                "created_at) VALUES (1, 1, 1, NULL, NULL, '', 'manual', 'Deleted by you.', '', "
                "0, :a, :c, '[]', 'applied', :t)"
            ),
            {
                "a": json.dumps([{"kind": "delete"}]),
                "c": json.dumps({"add": [], "remove": [], "trash": True}),
                "t": STAMP,
            },
        )
    eng.dispose()
    return f


def read(f, sql):
    eng = make_engine(f)
    try:
        with eng.begin() as c:
            return c.execute(text(sql)).all()
    finally:
        eng.dispose()


def test_local_archive_mail_becomes_offloaded(tmp_path):
    f = old_database(tmp_path)
    run_migrations(f)
    assert read(f, "SELECT version_num FROM alembic_version") == [("0013",)]
    assert read(f, "SELECT id, state, offload_id FROM mail_messages ORDER BY id") == [
        (1, "offloaded", None),
        (2, "in_mail", None),
        (3, "offloaded", None),
    ]


def test_the_action_delete_becomes_offload(tmp_path):
    f = old_database(tmp_path)
    run_migrations(f)
    actions = {i: json.loads(a) for i, a in read(f, "SELECT id, actions FROM rules")}
    assert actions[1] == [{"kind": "offload"}]
    assert actions[2] == [{"kind": "label", "label": "delete"}, {"kind": "archive"}]
    ((snapshot,),) = read(f, "SELECT snapshot FROM rule_versions")
    assert json.loads(snapshot) == {"name": "Junk", "actions": [{"kind": "offload"}]}
    ((acts, changes),) = read(f, "SELECT actions, changes FROM rule_decisions")
    assert json.loads(acts) == [{"kind": "offload"}]
    assert json.loads(changes)["trash"] is True


def test_downgrade_gives_the_old_action_name_back_and_keeps_mail_offloaded(tmp_path):
    f = old_database(tmp_path)
    run_migrations(f)
    _downgrade(f, "0010")
    ((acts,),) = read(f, "SELECT actions FROM rules WHERE id = 1")
    assert json.loads(acts) == [{"kind": "delete"}]
    assert read(f, "SELECT state FROM mail_messages WHERE id = 1") == [("offloaded",)]


def test_a_suggested_decisions_actions_are_renamed_too(tmp_path):
    f = old_database(tmp_path)
    eng = make_engine(f)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO rule_decisions (id, account_id, message_id, rule_id, rule_version, "
                "rule_name, how, why, model, took_ms, actions, changes, labels_seen, status, "
                "created_at) VALUES (2, 1, 2, 1, 1, 'Junk', 'ai', 'looks like junk', '', 0, "
                ":a, '{}', '[]', 'suggested', :t)"
            ),
            {
                "a": json.dumps([{"kind": "label", "label": "delete"}, {"kind": "delete"}]),
                "t": STAMP,
            },
        )
    eng.dispose()
    run_migrations(f)
    ((acts, status),) = read(f, "SELECT actions, status FROM rule_decisions WHERE id = 2")
    assert status == "suggested"
    assert json.loads(acts) == [{"kind": "label", "label": "delete"}, {"kind": "offload"}]
