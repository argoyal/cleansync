import sqlite3
from datetime import date

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from app.database.backup import backup_database
from app.database.engine import integrity_ok, make_engine
from app.database.migrate import run_migrations
from app.models import Base


def test_pragmas_are_safe_for_network_and_windows_drives(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    with engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "delete"
        assert c.execute(text("PRAGMA synchronous")).scalar() == 2  # FULL
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert c.execute(text("PRAGMA busy_timeout")).scalar() == 10000


def test_migrations_match_models(tmp_path):
    db_file = tmp_path / "m.db"
    run_migrations(db_file)
    engine = make_engine(db_file)
    with engine.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diff == []


def test_integrity_check_passes_on_fresh_db(tmp_path):
    db_file = tmp_path / "i.db"
    run_migrations(db_file)
    assert integrity_ok(make_engine(db_file)) is True


def test_backup_copies_db_and_keeps_only_newest(tmp_path):
    db_file = tmp_path / "b.db"
    run_migrations(db_file)
    out = tmp_path / "backups"
    for day in range(1, 18):
        backup_database(db_file, out, today=date(2026, 1, day), keep=14)
    files = sorted(p.name for p in out.iterdir())
    assert len(files) == 14
    assert files[0] == "drivesync-2026-01-04.db"
    assert files[-1] == "drivesync-2026-01-17.db"
    con = sqlite3.connect(out / files[-1])
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert "sync_pairs" in tables
