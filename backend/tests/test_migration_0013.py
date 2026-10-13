"""Migration 0013 (bulk offload spec §3, §5): the permanent delete switch, and what kind
of job an offload row is."""

from sqlalchemy import text

from app.database.engine import make_engine
from app.database.migrate import run_migrations
from tests.test_mail_speed_schema import _downgrade

STAMP = "2026-01-02 03:04:05.000000"


def _db(tmp_path):
    f = tmp_path / "v.db"
    run_migrations(f, "0012")
    with make_engine(f).begin() as c:
        c.execute(
            text(
                "INSERT INTO accounts (id, email, remote_name, status, connected_at) "
                "VALUES (1, 'a@x.com', 'a', 'CONNECTED', :t)"
            ),
            {"t": STAMP},
        )
        c.execute(
            text(
                "INSERT INTO mail_offloads (id, account_id, filter_text, state, total, done, "
                "skipped, bytes, created_at) VALUES (1, 1, 'older_than:1y', 'DONE', 3, 3, 0, "
                "30, :t)"
            ),
            {"t": STAMP},
        )
    return f


def _read(f, sql):
    with make_engine(f).connect() as c:
        return c.execute(text(sql)).all()


def test_existing_rows_get_the_safe_defaults(tmp_path):
    f = _db(tmp_path)
    run_migrations(f)
    assert _read(f, "SELECT permanent_delete, permanent_delete_note FROM accounts") == [(0, None)]
    assert _read(f, "SELECT kind, removed, stop_asked FROM mail_offloads") == [("offload", 0, 0)]


def test_downgrade_drops_the_columns_and_the_delete_forever_jobs(tmp_path):
    f = _db(tmp_path)
    run_migrations(f)
    with make_engine(f).begin() as c:
        c.execute(
            text(
                "INSERT INTO mail_offloads (id, account_id, filter_text, state, total, done, "
                "skipped, bytes, created_at, kind, removed, stop_asked) VALUES (2, 1, 'Inbox', "
                "'DONE', 1, 1, 0, 9, :t, 'delete_forever', 1, 0)"
            ),
            {"t": STAMP},
        )
    _downgrade(f, "0012")
    assert _read(f, "SELECT id FROM mail_offloads") == [(1,)]  # old code reads it as an offload
    assert "permanent_delete" not in [r[1] for r in _read(f, "PRAGMA table_info(accounts)")]
    assert "kind" not in [r[1] for r in _read(f, "PRAGMA table_info(mail_offloads)")]


def test_offload_ids_are_never_reused_after_either_direction(tmp_path):
    f = _db(tmp_path)
    for step in ("up", "down"):
        if step == "up":
            run_migrations(f)
        else:
            _downgrade(f, "0012")
        sql = _read(f, "SELECT sql FROM sqlite_master WHERE name = 'mail_offloads'")[0][0]
        assert "AUTOINCREMENT" in sql.upper()
