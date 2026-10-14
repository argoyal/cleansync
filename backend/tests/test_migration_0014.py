"""Migration 0014 (0.5.2): unsubscribe headers on mail, sender statuses and jobs."""

from sqlalchemy import text

from app.database.engine import make_engine
from app.database.migrate import run_migrations
from tests.test_mail_speed_schema import _downgrade

STAMP = "2026-01-02 03:04:05.000000"
TRIGGERS = "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='mail_messages'"


def _read(f, sql):
    with make_engine(f).connect() as c:
        return c.execute(text(sql)).all()


def _db(tmp_path):
    f = tmp_path / "v.db"
    run_migrations(f, "0013")
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
                "INSERT INTO mail_messages (id, account_id, provider_id, thread_id, from_addr, "
                "to_addrs, cc_addrs, subject, date, size, file_size, sha256, labels, unread, "
                "starred, has_attachments, attachments, snippet, file_path, state, "
                "first_seen_at, updated_at) VALUES (1, 1, 'p1', 't1', '', '', '', 's', :t, 1, "
                "1, 'x', '[]', 0, 0, 0, '[]', '', '2026/01/p1.eml', 'IN_MAIL', :t, :t)"
            ),
            {"t": STAMP},
        )
    return f


def test_old_mail_starts_unscanned_and_the_triggers_survive(tmp_path):
    f = _db(tmp_path)
    before = _read(f, TRIGGERS)
    assert before[0][0] > 0
    run_migrations(f)
    assert _read(f, "SELECT headers_scanned, list_unsubscribe, sender FROM mail_messages") == [
        (0, None, None)
    ]
    assert _read(f, TRIGGERS) == before
    names = {r[0] for r in _read(f, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"unsubscribe_senders", "unsubscribe_jobs", "unsubscribe_job_items"} <= names
    cols = [r[1] for r in _read(f, "PRAGMA table_info(accounts)")]
    assert {"gmail_filters", "gmail_filters_note", "photo_type", "photo_updated_at"} <= set(cols)
    assert _read(f, "SELECT gmail_filters FROM accounts") == [(0,)]


def test_downgrade_and_upgrade_again(tmp_path):
    f = _db(tmp_path)
    run_migrations(f)
    _downgrade(f, "0013")
    cols = [r[1] for r in _read(f, "PRAGMA table_info(mail_messages)")]
    assert "headers_scanned" not in cols and "sender" not in cols
    assert "photo_type" not in [r[1] for r in _read(f, "PRAGMA table_info(accounts)")]
    assert _read(f, "SELECT id FROM mail_messages") == [(1,)]
    run_migrations(f)
    assert _read(f, "SELECT headers_scanned FROM mail_messages") == [(0,)]
