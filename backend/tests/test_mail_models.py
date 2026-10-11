from datetime import UTC, datetime

from sqlalchemy import select, text

from app.models import Account, MailMessage
from app.models.enums import MailState


def _msg(account_id, provider_id="18a0000000000001", **kw):
    fields = (
        dict(
            account_id=account_id,
            provider_id=provider_id,
            thread_id="t1",
            subject="Hello",
            date=datetime(2026, 10, 1, tzinfo=UTC),
            size=1200,
            file_size=1100,
            sha256="0" * 64,
            labels=["INBOX"],
            unread=True,
            starred=False,
            has_attachments=False,
            attachments=[],
            file_path="2026/10/18a0000000000001.eml",
        )
        | kw
    )
    return MailMessage(**fields)


def test_a_mail_row_has_sensible_defaults_and_the_search_table_works(db, make_account):
    acc = make_account()
    m = _msg(acc.id)
    db.add(m)
    db.commit()
    assert m.state == MailState.IN_MAIL and m.offload_id is None
    db.execute(
        text(
            "INSERT INTO mail_fts(rowid, subject, from_addr, to_addrs, body) VALUES (:i, :s, :f, :t, :b)"
        ),
        {
            "i": m.id,
            "s": "Hello",
            "f": "bank@example.com",
            "t": "alice@example.com",
            "b": "your statement",
        },
    )
    found = (
        db.execute(text("SELECT rowid FROM mail_fts WHERE mail_fts MATCH 'statement'"))
        .scalars()
        .all()
    )
    assert found == [m.id]


def test_mail_rows_go_with_their_account(db, make_account):
    acc = make_account()
    db.add(_msg(acc.id))
    db.commit()
    db.delete(db.get(Account, acc.id))
    db.commit()
    assert db.scalars(select(MailMessage)).all() == []


def test_mail_lives_next_to_storage_never_inside_it(layout):
    assert layout.mail_root == layout.root / "mail"
    assert layout.mail_root.is_dir()
    assert not layout.mail_root.is_relative_to(layout.storage)
    assert layout.mail_dir("Alice@Example.com") == layout.mail_root / "alice@example.com"
    assert layout.mail_job_log(7).name == "mail-7.log"
