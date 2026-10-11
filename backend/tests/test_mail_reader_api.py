from datetime import UTC, datetime, timedelta

from app.devtools.fake_gmail import MINI_PDF, TINY_PNG, make_mail
from app.models import MailLabel
from app.models.enums import MailState
from app.services.mail import index
from app.services.mail.adapter import MailMeta
from app.services.mail.store import eml_path, parse_eml, write_eml

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def _keep(
    app, db, acc, mid, raw, *, thread="t1", labels=("INBOX",), days=1, state=MailState.IN_MAIL
):
    rel = f"2026/10/{mid}.eml"
    size, sha = write_eml(app.state.layout, acc.email, rel, raw)
    meta = MailMeta(mid, thread, labels, NOW - timedelta(days=days), size, "1", "")
    row = index.add_message(db, acc.id, meta, parse_eml(raw), rel, size, sha, NOW)
    row.state = state
    db.commit()
    return row


def test_a_message_shows_its_details_labels_and_attachments(auth_client, app, db, make_account):
    acc = make_account()
    db.add(MailLabel(account_id=acc.id, provider_label_id="Label_2", name="Receipts", type="user"))
    db.commit()
    raw = make_mail(
        "Invoice",
        sender="Billing <billing@example.com>",
        html='<p>Hi</p><img src="https://t.example.com/p.gif">',
        attachments=(
            ("invoice.pdf", "application/pdf", MINI_PDF),
            ("photo.png", "image/png", TINY_PNG),
            ("data.bin", "application/octet-stream", b"\x00\x01"),
        ),
    )
    row = _keep(app, db, acc, "m1", raw, labels=("INBOX", "Label_2", "IMPORTANT"))
    body = auth_client.get(f"/api/mail/messages/{row.id}").json()
    assert body["subject"] == "Invoice" and body["from_addr"] == "Billing <billing@example.com>"
    assert body["labels"] == [{"id": "Label_2", "name": "Receipts"}]
    assert body["has_remote_images"] is True and body["file_missing"] is False
    assert body["gmail_url"] == "https://mail.google.com/mail/u/alice%40example.com/#all/m1"
    assert [(a["index"], a["name"], a["preview"]) for a in body["attachments"]] == [
        (0, "invoice.pdf", True),
        (1, "photo.png", True),
        (2, "data.bin", False),
    ]


def test_attachments_preview_with_a_sandbox_and_download_with_their_name(
    auth_client, app, db, make_account
):
    acc = make_account()
    raw = make_mail(
        "Files",
        attachments=(
            ("invoice.pdf", "application/pdf", MINI_PDF),
            ("photo.png", "image/png", TINY_PNG),
            ("page.html", "text/html", b"<script>x</script>"),
        ),
    )
    row = _keep(app, db, acc, "m1", raw)
    pdf = auth_client.get(f"/api/mail/messages/{row.id}/attachments/0")
    assert pdf.content == MINI_PDF and pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith("inline")
    assert pdf.headers["x-content-type-options"] == "nosniff"
    img = auth_client.get(f"/api/mail/messages/{row.id}/attachments/1")
    assert img.headers["content-security-policy"].startswith("sandbox")
    page = auth_client.get(f"/api/mail/messages/{row.id}/attachments/2")
    assert page.headers["content-type"].startswith("text/plain")  # HTML never runs on our address
    assert page.headers["content-disposition"].startswith("attachment")
    down = auth_client.get(f"/api/mail/messages/{row.id}/attachments/0?download=1")
    assert down.headers["content-disposition"].startswith('attachment; filename="invoice.pdf"')
    assert auth_client.get(f"/api/mail/messages/{row.id}/attachments/9").status_code == 404
    eml = auth_client.get(f"/api/mail/messages/{row.id}/eml")
    assert eml.content == raw and eml.headers["content-type"] == "message/rfc822"
    assert 'filename="Files.eml"' in eml.headers["content-disposition"]


def test_svg_attachments_are_only_downloaded_and_hostile_names_stay_in_the_header(
    auth_client, app, db, make_account
):
    acc = make_account()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'
    raw = make_mail("Pics", attachments=(('a"b\\ é;x.svg', "image/svg+xml", svg),))
    row = _keep(app, db, acc, "m1", raw)
    assert (
        auth_client.get(f"/api/mail/messages/{row.id}").json()["attachments"][0]["preview"] is False
    )
    r = auth_client.get(f"/api/mail/messages/{row.id}/attachments/0")
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["content-disposition"].startswith('attachment; filename="a_b_ _;x.svg"')
    assert "filename*=UTF-8''a%22b%5C%20%C3%A9%3Bx.svg" in r.headers["content-disposition"]


def test_a_thread_lists_its_mails_oldest_first(auth_client, app, db, make_account):
    acc = make_account()
    _keep(app, db, acc, "a", make_mail("Plan"), days=3)
    _keep(app, db, acc, "b", make_mail("Re: Plan"), days=2, state=MailState.OFFLOADED)
    _keep(app, db, acc, "c", make_mail("Other"), thread="t2")
    rows = auth_client.get("/api/mail/threads/t1", params={"account_id": acc.id}).json()
    assert [r["subject"] for r in rows] == ["Plan", "Re: Plan"]
    assert rows[1]["state"] == "offloaded" and rows[1]["gmail_url"] is None
    assert auth_client.get("/api/mail/threads/none", params={"account_id": acc.id}).json() == []
    bad = auth_client.get("/api/mail/threads/a%20b%3B", params={"account_id": acc.id})
    assert bad.status_code == 404


def test_a_missing_file_says_so(auth_client, app, db, make_account):
    acc = make_account()
    row = _keep(app, db, acc, "m1", make_mail("Gone"))
    eml_path(app.state.layout, acc.email, row.file_path).unlink()
    body = auth_client.get(f"/api/mail/messages/{row.id}").json()
    assert body["file_missing"] is True and body["attachments"] == []
    r = auth_client.get(f"/api/mail/messages/{row.id}/body")
    assert (
        r.status_code == 404
        and r.json()["detail"] == "This mail's file is missing on the storage disk."
    )
    assert auth_client.get("/api/mail/threads", params={"account_id": acc.id}).status_code == 200
    assert auth_client.get("/api/mail/messages/999").status_code == 404
