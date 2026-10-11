import hashlib
import shutil
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import pytest
from sqlalchemy import text

from app.devtools.fake_gmail import MINI_PDF, make_mail
from app.models import MailMessage
from app.services.mail import index
from app.services.mail.adapter import MailMeta
from app.services.mail.store import (
    MailRootMissing,
    MailStoreError,
    check_eml,
    eml_path,
    eml_rel,
    html_to_text,
    parse_eml,
    sweep_temp_files,
    write_eml,
)

WHEN = datetime(2025, 3, 4, 10, 30, tzinfo=UTC)
EMAIL = "alice@example.com"


def test_a_mail_goes_in_its_year_and_month_by_gmails_date():
    assert eml_rel("18a0000000000001", WHEN) == "2025/03/18a0000000000001.eml"
    with pytest.raises(MailStoreError):
        eml_rel("../x", WHEN)


def test_a_file_is_written_whole_and_nothing_else_is_left_behind(layout):
    raw = make_mail("Hello", when=WHEN)
    size, sha = write_eml(layout, EMAIL, "2025/03/a1.eml", raw)
    path = eml_path(layout, EMAIL, "2025/03/a1.eml")
    assert path.read_bytes() == raw and size == len(raw)
    assert sha == hashlib.sha256(raw).hexdigest()
    assert [p.name for p in path.parent.iterdir()] == ["a1.eml"]


def test_nothing_is_written_when_the_mail_folder_is_missing(layout):
    shutil.rmtree(layout.mail_root)  # the disk dropped out: the mount point is empty
    with pytest.raises(MailRootMissing):
        write_eml(layout, EMAIL, "2025/03/a1.eml", b"x")
    assert not layout.mail_root.exists()  # never made again onto the empty mount point


def test_check_eml_says_why_a_file_is_not_the_one_downloaded(layout):
    raw = make_mail("Hello", when=WHEN)
    size, sha = write_eml(layout, EMAIL, "2025/03/a1.eml", raw)
    path = eml_path(layout, EMAIL, "2025/03/a1.eml")
    assert check_eml(path, size, sha) is None
    path.write_bytes(raw[:-1] + b"!")
    assert check_eml(path, size, sha) == "the file's contents changed"
    path.write_bytes(raw[:-5])
    assert check_eml(path, size, sha) == "the file's size changed"
    junk = b"\x00" * size
    path.write_bytes(junk)
    assert (
        check_eml(path, size, hashlib.sha256(junk).hexdigest()) == "the file doesn't read as a mail"
    )
    path.unlink()
    assert check_eml(path, size, sha) == "the file is missing"


def test_parse_reads_headers_attachments_and_text_for_search():
    raw = make_mail(
        "Résumé",
        sender="Bank <bank@example.com>",
        html="<p>Your <b>statement</b></p><script>steal()</script>",
        attachments=(("june.pdf", "application/pdf", MINI_PDF),),
        inline_png_cid="logo",
    )
    p = parse_eml(raw)
    assert p.subject == "Résumé"
    assert p.from_addr == "Bank <bank@example.com>" and p.to_addrs == "alice@example.com"
    assert p.attachments == [{"name": "june.pdf", "type": "application/pdf", "size": len(MINI_PDF)}]
    assert "Hello there." in p.body_text  # the text part wins over the HTML part
    assert p.message_id and p.message_id.startswith("<")


def test_parse_never_raises_on_broken_mail():
    p = parse_eml(b"Subject: =?broken\r\nFrom: \x00\x01\r\n\r\n\xff\xfe body")
    assert isinstance(p.subject, str) and p.attachments == []
    assert html_to_text("<style>x{}</style><p>a &amp; b</p><script>no</script>") == "a & b"


def test_temp_files_left_by_a_crash_are_swept(layout):
    folder = layout.mail_dir(EMAIL) / "2025" / "03"
    folder.mkdir(parents=True)
    (folder / ".tmp-abc-a1.eml").write_bytes(b"half")
    (folder / "a2.eml").write_bytes(b"whole")
    assert sweep_temp_files(layout) == 1
    assert [p.name for p in folder.iterdir()] == ["a2.eml"]


def _meta(mid="a1", labels=("INBOX", "UNREAD")):
    return MailMeta(mid, "t1", labels, WHEN, 1234, "1001", "Your statement is ready")


def test_a_row_is_added_with_its_search_text(db, make_account):
    acc = make_account()
    raw = make_mail("Statement", sender="bank@example.com", text="June statement attached")
    row = index.add_message(
        db, acc.id, _meta(), parse_eml(raw), "2025/03/a1.eml", len(raw), "0" * 64, WHEN
    )
    assert row.unread and not row.starred and row.labels == ["INBOX", "UNREAD"]
    assert index.stored_ids(db, acc.id, ["a1", "zz"]) == {"a1"}
    assert index.count_messages(db, acc.id) == 1 and index.stored_bytes(db, acc.id) == len(raw)
    hit = db.execute(text("SELECT rowid FROM mail_fts WHERE mail_fts MATCH 'june'")).scalars().all()
    assert hit == [row.id]
    index.delete_search_rows(db, [row.id])
    assert db.execute(text("SELECT count(*) FROM mail_fts")).scalar() == 0
    assert db.get(MailMessage, row.id) is not None


def test_failures_back_off_and_show_after_three_tries(db, make_account):
    acc = make_account()
    now = WHEN
    for attempt in (1, 2):
        f = index.record_failure(db, acc.id, "bad1", "HTTP 500", now)
        assert f.attempts == attempt
        assert index.waiting_failures(db, acc.id, ["bad1"], now) == {"bad1"}
        assert index.due_failures(db, acc.id, now) == []
        now += index.retry_after(attempt) + timedelta(seconds=1)
        assert index.due_failures(db, acc.id, now) == ["bad1"]
    assert index.shown_failures(db, acc.id) == []
    index.record_failure(db, acc.id, "bad1", "HTTP 500", now)
    assert [f.provider_id for f in index.shown_failures(db, acc.id)] == ["bad1"]
    assert index.retry_after(20) == timedelta(days=1)  # at most once a day
    index.clear_failure(db, acc.id, "bad1")
    assert index.shown_failures(db, acc.id) == []


def test_a_mail_with_only_an_attachment_parses():
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = "Scan", "a@example.com", "b@example.com"
    msg.set_content(MINI_PDF, maintype="application", subtype="pdf", filename="a.pdf")
    raw = msg.as_bytes()
    p = parse_eml(raw)
    assert p.body_text == "" and [a["name"] for a in p.attachments] == ["a.pdf"]


def test_hostile_mail_parses_without_hanging():
    import time

    deep = (
        b"Content-Type: multipart/mixed; boundary=b\r\n\r\n"
        + b"--b\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n" * 200
    )
    many = (
        b"Content-Type: multipart/mixed; boundary=b\r\n\r\n"
        + (
            b"--b\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=x.pdf\r\n\r\nabc\r\n"
        )
        * 2000
        + b"--b--\r\n"
    )
    start = time.monotonic()
    assert isinstance(parse_eml(deep).subject, str)
    assert len(parse_eml(many).attachments) >= 1
    for bad in ("<a " * 100000, "<!--" * 100000):
        html_to_text(bad)
        raw = b"Content-Type: text/html\r\n\r\n" + bad.encode()
        parse_eml(raw)
    big = b"Content-Type: text/plain\r\n\r\n" + b"word " * 2_000_000
    assert len(parse_eml(big).body_text) == 32_000  # search keeps the first 32,000 characters
    assert time.monotonic() - start < 5
