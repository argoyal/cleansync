"""Migration 0012: a run of rules keeps its own rules, view and search (running rules spec §8)."""

from sqlalchemy import text

from app.database.engine import make_engine
from app.database.migrate import run_migrations
from tests.test_mail_speed_schema import _downgrade

STAMP = "2026-01-02 03:04:05.000000"


def read(f, sql):
    eng = make_engine(f)
    try:
        with eng.begin() as c:
            return c.execute(text(sql)).all()
    finally:
        eng.dispose()


def test_old_runs_mean_every_rule_and_the_way_back_works(tmp_path):
    f = tmp_path / "v.db"
    run_migrations(f, "0011")
    eng = make_engine(f)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO accounts (id, email, remote_name, status, connected_at) "
                "VALUES (1, 'a@x.com', 'a', 'CONNECTED', :t)"
            ),
            {"t": STAMP},
        )
        c.execute(
            text(
                "INSERT INTO rule_jobs (id, account_id, filter_text, state, total, decided, "
                "applied, skipped, failed, created_at) "
                "VALUES (1, 1, 'older_than:1y', 'DONE', 3, 3, 2, 1, 0, :t)"
            ),
            {"t": STAMP},
        )
    eng.dispose()
    run_migrations(f)
    assert read(f, "SELECT version_num FROM alembic_version") == [("0013",)]
    assert read(f, "SELECT id, rule_ids, view, q, filter_text FROM rule_jobs") == [
        (1, "[]", "", "", "older_than:1y")
    ]
    _downgrade(f, "0011")
    assert read(f, "SELECT id, filter_text, applied FROM rule_jobs") == [(1, "older_than:1y", 2)]
    columns = {r[1] for r in read(f, "PRAGMA table_info(rule_jobs)")}
    assert not {"rule_ids", "view", "q"} & columns
    (sql,) = read(f, "SELECT sql FROM sqlite_master WHERE name = 'rule_jobs'")[0]
    assert "AUTOINCREMENT" in sql
