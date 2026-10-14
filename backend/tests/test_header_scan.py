"""The one-off header scan (0.5.2 §3.1) and the headers saved at backup."""

from datetime import timedelta

from sqlalchemy import text

from app.devtools.fake_gmail import make_mail
from app.services.mail import header_scan
from app.services.mail.header_scan import read_headers, scan_progress
from app.services.mail.store import eml_path
from tests.offload_helpers import NOW

UNSUB = "<mailto:unsubscribe@shop.example.com>"


def _add_news(box):
    when = NOW - timedelta(days=3)
    box.fake.add(
        make_mail(
            "Big sale",
            sender="deals@shop.example.com",
            when=when,
            headers={"List-Unsubscribe": UNSUB},
        ),
        labels=("INBOX",),
        when=when,
    )


def _make_old(box):
    with box.app.state.session_factory() as db:
        db.execute(
            text("UPDATE mail_messages SET headers_scanned=0, list_unsubscribe=NULL, sender=NULL")
        )
        db.commit()


async def _old_box(box):
    _add_news(box)
    await box.backup()
    _make_old(box)


async def test_backup_saves_headers_and_sender(offload_box):
    box = offload_box
    _add_news(box)
    await box.backup()
    row = box.row("Big sale")
    assert row.list_unsubscribe == UNSUB
    assert row.sender == "deals@shop.example.com"
    assert row.headers_scanned is True
    assert box.row("Yesterday").list_unsubscribe is None


def test_read_headers_unreadable_file_is_none(tmp_path):
    assert read_headers(tmp_path / "nope.eml") is None


async def test_scan_fills_old_rows(offload_box):
    box = offload_box
    await _old_box(box)
    await box.mail.handle(box.acc.id, "header_scan", {}, "SCHEDULE")
    rows = box.rows_all()
    assert rows and all(r.headers_scanned for r in rows)
    assert all(r.sender for r in rows)
    assert box.row("Big sale").list_unsubscribe == UNSUB
    assert box.row("Yesterday").list_unsubscribe is None
    assert box.queue.requests == []


async def test_scan_works_in_runs(offload_box, monkeypatch):
    box = offload_box
    await _old_box(box)
    monkeypatch.setattr(header_scan, "RUN_LIMIT", 2)
    monkeypatch.setattr(header_scan, "CHUNK", 1)
    await box.mail.handle(box.acc.id, "header_scan", {}, "SCHEDULE")
    assert sum(r.headers_scanned for r in box.rows_all()) == 2
    assert (box.acc.id, "header_scan", {}) in [r[:3] for r in box.queue.requests]
    await box.run_queued()
    assert all(r.headers_scanned for r in box.rows_all())


async def test_unreadable_file_is_marked_scanned_without_header(offload_box):
    box = offload_box
    await _old_box(box)
    row = box.row("Big sale")
    eml_path(box.app.state.layout, box.acc.email, row.file_path).unlink()
    await box.mail.handle(box.acc.id, "header_scan", {}, "SCHEDULE")
    again = box.row("Big sale")
    assert again.headers_scanned is True and again.list_unsubscribe is None
    assert box.queue.requests == []


async def test_missing_disk_marks_nothing(offload_box, monkeypatch):
    box = offload_box
    await _old_box(box)

    async def stopped():
        return True

    monkeypatch.setattr(box.mail, "stopped", stopped)
    await box.mail.handle(box.acc.id, "header_scan", {}, "SCHEDULE")
    assert not any(r.headers_scanned for r in box.rows_all())
    assert box.queue.requests == []


async def test_tick_asks_for_a_scan_only_when_needed(offload_box, monkeypatch):
    box = offload_box
    scan = box.app.state.header_scan
    await box.backup()  # new rows are already scanned
    scan.ask_when_needed()
    assert box.queue.requests == []
    _make_old(box)
    scan.ask_when_needed()
    assert [r[:3] for r in box.queue.requests] == [(box.acc.id, "header_scan", {})]
    box.queue.requests.clear()
    monkeypatch.setattr(box.queue, "is_busy", lambda _id: True)
    scan.ask_when_needed()
    assert box.queue.requests == []
    assert scan.ask_when_needed in box.mail.tick_hooks


async def test_scan_progress(offload_box):
    box = offload_box
    await _old_box(box)
    total = len(box.rows_all())
    with box.app.state.session_factory() as db:
        assert scan_progress(db, box.acc.id) == (0, total)
    await box.mail.handle(box.acc.id, "header_scan", {}, "SCHEDULE")
    with box.app.state.session_factory() as db:
        assert scan_progress(db, box.acc.id) == (total, total)
