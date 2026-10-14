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
    from app.database.fts import include_name

    db_file = tmp_path / "m.db"
    run_migrations(db_file)
    engine = make_engine(db_file)
    with engine.connect() as c:
        ctx = MigrationContext.configure(c, opts={"include_name": include_name})
        diff = compare_metadata(ctx, Base.metadata)
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


def test_a_v0_2_1_database_upgrades_to_ai_rules(tmp_path):
    """0.2.1 shipped migration 0008 (mail speed); the AI rules migration is 0009 on top of it.
    A 0.2.1 database keeps its mail and label rows, gets the rules tables, and the label
    triggers still work afterwards."""
    from alembic.script import ScriptDirectory

    from app.database.migrate import _MIGRATIONS

    script = ScriptDirectory(str(_MIGRATIONS))
    assert script.get_heads() == ["0014"]
    assert script.get_revision("0010").down_revision == "0009"
    assert script.get_revision("0013").down_revision == "0012"
    assert script.get_revision("0012").down_revision == "0011"
    assert script.get_revision("0011").down_revision == "0010"
    assert script.get_revision("0009").down_revision == "0008"
    assert script.get_revision("0008").down_revision == "0007"

    f = tmp_path / "v021.db"
    run_migrations(f, "0008")
    eng = make_engine(f)
    stamp = "2026-01-02 03:04:05.000000"
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO accounts (id, email, remote_name, status, connected_at) "
                "VALUES (1, 'a@x.com', 'a', 'CONNECTED', :t)"
            ),
            {"t": stamp},
        )
        c.execute(
            text(
                "INSERT INTO mail_messages (account_id, provider_id, thread_id, from_addr, "
                "to_addrs, cc_addrs, subject, date, size, file_size, sha256, labels, unread, "
                "starred, has_attachments, attachments, snippet, file_path, state, "
                "first_seen_at, updated_at) VALUES (1, 'm1', 't1', '', '', '', '', :t, 10, 12, "
                ":sha, '[\"INBOX\"]', 0, 0, 0, '[]', '', 'x', 'IN_MAIL', :t, :t)"
            ),
            {"t": stamp, "sha": "0" * 64},
        )
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0008"
    eng.dispose()
    run_migrations(f)
    eng = make_engine(f)
    with eng.begin() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0014"
        tables = {r[0] for r in c.execute(text("SELECT name FROM sqlite_master")).all()}
        assert {"rules", "rule_todo", "rule_decisions", "mail_message_labels"} <= tables
        assert {"mail_labels_ai", "mail_labels_au", "mail_labels_ad"} <= tables
        c.execute(text("UPDATE mail_messages SET labels = '[\"SENT\"]' WHERE provider_id = 'm1'"))
        got = c.execute(text("SELECT label_id FROM mail_message_labels")).scalars().all()
    eng.dispose()
    assert got == ["SENT"]
