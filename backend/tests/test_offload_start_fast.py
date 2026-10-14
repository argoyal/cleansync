"""0.5.2 §9: the preview and Start never stat a mail's file; rows go in with bulk inserts."""

from sqlalchemy import func, select, text

from app.models import MailOffload, MailOffloadItem
from app.services.mail import offload_plan
from tests.offload_helpers import offload_box  # noqa: F401


def _boom(*_a, **_k):
    raise AssertionError("a file was checked during the preview or Start")


async def test_preview_and_start_touch_no_file(offload_box, auth_client, monkeypatch):  # noqa: F811
    box = offload_box
    await box.backup()
    # Any stat() of a mail file during preview/start fails the test.
    monkeypatch.setattr(offload_plan, "eml_path", _boom)
    body = {"account_id": box.acc.id, "q": "older_than:1y"}
    p = auth_client.post("/api/mail/offloads/preview", json=body)
    assert p.status_code == 200, p.text
    n = p.json()["count"]
    assert n == 3
    r = auth_client.post("/api/mail/offloads", json={**body, "confirm": n})
    assert r.status_code == 202, r.text
    with box.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(MailOffloadItem)) == 3


async def test_row_without_a_recorded_file_is_left_out(offload_box, auth_client):  # noqa: F811
    box = offload_box
    await box.backup()
    with box.app.state.session_factory() as db:
        row = db.get(type(box.row("Old photos")), box.row("Old photos").id)
        row.file_size = 0
        db.commit()
    p = auth_client.post(
        "/api/mail/offloads/preview", json={"account_id": box.acc.id, "q": "older_than:1y"}
    )
    assert p.json()["count"] == 2
    assert any(e["reason"] == "not_backed_up" for e in p.json()["excluded"])


async def test_count_changed_is_still_refused_at_once(offload_box, auth_client):  # noqa: F811
    box = offload_box
    await box.backup()
    r = auth_client.post(
        "/api/mail/offloads",
        json={"account_id": box.acc.id, "q": "older_than:1y", "confirm": 99},
    )
    assert r.status_code == 409
    assert "changed since the preview (now 3)" in r.json()["detail"]


def test_insert_items_chunks(db, make_account):
    acc = make_account()
    rows = [{"a": acc.id, "p": f"m{i}", "d": "2026-01-01 00:00:00.000000"} for i in range(2500)]
    db.execute(
        text(
            "INSERT INTO mail_messages (account_id, provider_id, thread_id, from_addr, to_addrs, "
            "cc_addrs, subject, date, size, file_size, sha256, labels, unread, starred, "
            "has_attachments, attachments, snippet, file_path, state, first_seen_at, updated_at) "
            "VALUES (:a, :p, 't', 'x', 'x', 'x', 's', :d, 1, 1, '', '[]', 0, 0, 0, '[]', '', "
            "'x', 'in_mail', :d, :d)"
        ),
        rows,
    )
    off = MailOffload(account_id=acc.id, filter_text="x", total=0)
    db.add(off)
    db.commit()
    ids = list(db.scalars(text("SELECT id FROM mail_messages")))
    offload_plan.insert_items(db, off.id, ids)
    db.commit()
    assert db.scalar(select(func.count()).select_from(MailOffloadItem)) == 2500
