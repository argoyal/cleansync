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


def test_autoincrement_migration_keeps_existing_rows(tmp_path):
    db_file = tmp_path / "u.db"
    run_migrations(db_file, revision="0001")
    con = sqlite3.connect(db_file)
    con.executescript(
        """
        INSERT INTO accounts (id, email, remote_name, status, connected_at)
            VALUES (1, 'alice@example.com', 'gd_a', 'CONNECTED', '2026-10-01');
        INSERT INTO sync_pairs (id, account_id, name, local_subpath, remote_path, direction,
            enabled, paused, max_deletions, backups_enabled, initialized, needs_resync,
            override_next, status, fail_count, created_at, updated_at)
            VALUES (1, 1, 'Docs', '', '', 'BIDIRECTIONAL', 1, 0, 10, 1, 1, 0, 0, 'HEALTHY', 0,
            '2026-10-01', '2026-10-01');
        INSERT INTO sync_jobs (id, pair_id, trigger, kind, state, files_uploaded,
            files_downloaded, files_deleted_local, files_deleted_remote, files_changed,
            conflicts, planned_deletions, bytes_transferred, override_used)
            VALUES (7, 1, 'MANUAL', 'BISYNC', 'SUCCESS', 0, 0, 0, 0, 0, 0, 0, 0, 0);
        INSERT INTO conflicts (pair_id, path, local_variant, remote_variant, state, created_at)
            VALUES (1, 'a', 'a.conflict1', 'a.conflict2', 'OPEN', '2026-10-01');
        """
    )
    con.commit()
    con.close()
    run_migrations(db_file)
    engine = make_engine(db_file)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM sync_jobs")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM conflicts")).scalar() == 1
        sql = c.execute(
            text("SELECT sql FROM sqlite_master WHERE name IN ('sync_pairs', 'sync_jobs')")
        ).scalars()
        assert all("AUTOINCREMENT" in s for s in sql)
        assert c.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    assert integrity_ok(engine)


def test_backup_never_leaves_a_half_written_file(tmp_path, monkeypatch):
    import pytest

    db_file = tmp_path / "b.db"
    run_migrations(db_file)
    out = tmp_path / "backups"
    first = backup_database(db_file, out, today=date(2026, 1, 1))
    good = first.read_bytes()

    class Broken:
        def __init__(self, real):
            self.real = real

        def backup(self, dst):
            raise sqlite3.OperationalError("disk I/O error")

        def close(self):
            self.real.close()

    real_connect = sqlite3.connect

    def connect(path, *a, **kw):
        con = real_connect(path, *a, **kw)
        return Broken(con) if str(path) == str(db_file) else con

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        backup_database(db_file, out, today=date(2026, 1, 1))
    with pytest.raises(sqlite3.OperationalError):
        backup_database(db_file, out, today=date(2026, 1, 2))
    assert first.read_bytes() == good
    assert sorted(p.name for p in out.iterdir()) == ["drivesync-2026-01-01.db"]


def test_backup_keep_must_be_at_least_one(tmp_path):
    import pytest

    db_file = tmp_path / "b.db"
    run_migrations(db_file)
    with pytest.raises(ValueError):
        backup_database(db_file, tmp_path / "backups", today=date(2026, 1, 1), keep=0)
