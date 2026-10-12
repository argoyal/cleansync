"""Migration 0008 (issue #16): the label table follows mail_messages by itself, and the
indexes the list, counts and status read from exist."""

from datetime import UTC, datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app.database.engine import make_engine
from app.database.migrate import _MIGRATIONS, run_migrations
from app.models import Account, MailMessage, MailMessageLabel
from app.models.enums import MailState

WHEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _mail(acc_id: int, pid: str, labels: list[str], *, thread: str | None = None, size=100):
    return MailMessage(
        account_id=acc_id,
        provider_id=pid,
        thread_id=thread or pid,
        date=WHEN,
        size=size,
        file_size=120,
        sha256="0" * 64,
        labels=labels,
        file_path=f"x/{pid}.eml",
        state=MailState.IN_MAIL,
    )


def _labels(db, message_id: int) -> set[str]:
    return set(
        db.scalars(
            select(MailMessageLabel.label_id).where(MailMessageLabel.message_id == message_id)
        )
    )


def _downgrade(db_file, revision: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    eng = make_engine(db_file)
    try:
        with eng.connect() as c:
            c.exec_driver_sql("PRAGMA foreign_keys=OFF")
            c.commit()
            with c.begin():
                cfg.attributes["connection"] = c
                command.downgrade(cfg, revision)
    finally:
        eng.dispose()


def _label_rows(db) -> int:
    return db.scalar(text("SELECT count(*) FROM mail_message_labels"))


def test_labels_follow_insert_update_and_delete(db, make_account):
    acc = make_account()
    m = _mail(acc.id, "m1", ["INBOX", "Label_1", "UNREAD"])
    db.add(m)
    db.commit()
    assert _labels(db, m.id) == {"INBOX", "Label_1", "UNREAD"}
    m.labels = ["Label_1"]  # archived and read in Gmail
    db.commit()
    assert _labels(db, m.id) == {"Label_1"}
    db.delete(m)
    db.commit()
    assert _label_rows(db) == 0


def test_label_rows_carry_date_thread_and_size(db, make_account):
    acc = make_account()
    m = _mail(acc.id, "m1", ["INBOX"], thread="t9", size=4321)
    db.add(m)
    db.commit()
    row = db.scalar(select(MailMessageLabel).where(MailMessageLabel.message_id == m.id))
    assert (row.account_id, row.thread_id, row.size) == (acc.id, "t9", 4321)
    assert row.date.replace(tzinfo=UTC) == WHEN


def test_deleting_the_account_removes_its_label_rows(db, make_account):
    acc = make_account()
    db.add(_mail(acc.id, "m1", ["INBOX"]))
    db.commit()
    db.delete(db.get(Account, acc.id))
    db.commit()
    assert _label_rows(db) == 0


def test_upgrade_fills_labels_from_existing_mail_and_downgrade_works(tmp_path):
    f = tmp_path / "old.db"
    run_migrations(f, "0007")
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
                ":sha, '[\"INBOX\", \"SENT\"]', 0, 0, 0, '[]', '', 'x', 'IN_MAIL', :t, :t)"
            ),
            {"t": stamp, "sha": "0" * 64},
        )
    eng.dispose()
    run_migrations(f)
    eng = make_engine(f)
    with eng.connect() as c:
        got = c.execute(
            text("SELECT label_id, thread_id, size FROM mail_message_labels ORDER BY label_id")
        ).all()
        count = c.execute(text("SELECT count(*) FROM mail_messages")).scalar()
    eng.dispose()
    assert [tuple(r) for r in got] == [("INBOX", "t1", 10), ("SENT", "t1", 10)]
    assert count == 1
    _downgrade(f, "0007")  # the way back works too
    eng = make_engine(f)
    with eng.connect() as c:
        tables = {r[0] for r in c.execute(text("SELECT name FROM sqlite_master")).all()}
        count = c.execute(text("SELECT count(*) FROM mail_messages")).scalar()
    eng.dispose()
    assert "mail_message_labels" not in tables and "mail_labels_ai" not in tables
    assert count == 1
