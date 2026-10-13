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
    assert "filename*=UTF-8''a%22b_%20%C3%A9%3Bx.svg" in r.headers["content-disposition"]


def test_a_thread_lists_its_mails_oldest_first(auth_client, app, db, make_account):
    acc = make_account()
    _keep(app, db, acc, "a", make_mail("Plan"), days=3)
    _keep(app, db, acc, "b", make_mail("Re: Plan"), days=2, state=MailState.OFFLOADED)
    _keep(app, db, acc, "c", make_mail("Other"), thread="t2")
    rows = auth_client.get("/api/mail/threads/t1", params={"account_id": acc.id}).json()
    assert [r["subject"] for r in rows] == ["Plan"]
    off = auth_client.get(
        "/api/mail/threads/t1", params={"account_id": acc.id, "view": "offloaded"}
    ).json()
    assert [r["subject"] for r in off] == ["Re: Plan"]
    assert off[0]["state"] == "offloaded" and off[0]["gmail_url"] is None
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


def test_a_disguised_svg_is_never_previewed(auth_client, app, db, make_account):
    acc = make_account()
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>x</script></svg>"
    raw = make_mail(
        "Sneaky", attachments=(("a.svg", "image/svg+xml", svg), ("b.svg", "image/png", svg))
    )
    raw = raw.replace(b"image/svg+xml", b"image/svg+xml (c)")
    row = _keep(app, db, acc, "m1", raw)
    detail = auth_client.get(f"/api/mail/messages/{row.id}").json()["attachments"]
    assert detail[0]["preview"] is False and detail[0]["type"] == "image/svg+xml"
    r = auth_client.get(f"/api/mail/messages/{row.id}/attachments/0")
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")
    assert r.headers["content-type"] == "application/octet-stream"


def test_a_nul_in_the_type_does_not_break_the_response(auth_client, app, db, make_account):
    acc = make_account()
    raw = make_mail("Odd", attachments=(("a.bin", "application/x-thing", b"abc"),))
    raw = raw.replace(b"application/x-thing", b"image/svg+xml\x00")
    row = _keep(app, db, acc, "m1", raw)
    r = auth_client.get(f"/api/mail/messages/{row.id}/attachments/0")
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")


def test_text_keeps_its_own_charset_and_long_names_keep_their_extension(
    auth_client, app, db, make_account
):
    acc = make_account()
    long_name = "x" * 300 + ".pdf"
    raw = make_mail(
        "Txt",
        attachments=(
            ("n.txt", "text/plain", b"abc"),
            (long_name, "application/pdf", MINI_PDF),
            ("a/b\\c.png", "image/png", TINY_PNG),
        ),
    )
    raw = (
        raw.replace(b'charset="utf-8"', b'charset="iso-8859-1"') if b'name="n.txt"' in raw else raw
    )
    row = _keep(app, db, acc, "m1", raw)
    txt = auth_client.get(f"/api/mail/messages/{row.id}/attachments/0")
    assert txt.headers["content-type"].startswith("text/plain; charset=")
    pdf = auth_client.get(f"/api/mail/messages/{row.id}/attachments/1?download=1")
    assert 'filename="' + "x" * 196 + '.pdf"' in pdf.headers["content-disposition"]
    png = auth_client.get(f"/api/mail/messages/{row.id}/attachments/2?download=1")
    assert 'filename="a_b_c.png"' in png.headers["content-disposition"]


def test_a_thread_does_not_open_each_mail(auth_client, app, db, make_account, monkeypatch):
    acc = make_account()
    _keep(
        app,
        db,
        acc,
        "a",
        make_mail("One", attachments=(("f.pdf", "application/pdf", MINI_PDF),)),
        days=3,
    )
    _keep(app, db, acc, "b", make_mail("Two"), days=2)
    calls = []
    for mod, name in (
        ("app.api.mail_reader", "has_remote_images"),
        ("app.api.mail_reader", "read_eml_of"),
        ("app.services.mail.reader", "render_body"),
    ):
        monkeypatch.setattr(f"{mod}.{name}", lambda *a, **k: calls.append(1))
    rows = auth_client.get("/api/mail/threads/t1", params={"account_id": acc.id}).json()
    assert calls == [] and len(rows) == 2
    assert rows[0]["has_remote_images"] is None and rows[0]["attachments"][0]["name"] == "f.pdf"
    assert rows[0]["file_missing"] is False


def test_an_unreadable_attachment_is_a_clear_error(auth_client, app, db, make_account, monkeypatch):
    acc = make_account()
    row = _keep(
        app, db, acc, "m1", make_mail("Bad", attachments=(("f.pdf", "application/pdf", MINI_PDF),))
    )

    class Broken:
        def get_payload(self, decode=False):
            raise ValueError("bad")

    monkeypatch.setattr("app.services.mail.reader.attachment_parts", lambda msg: [Broken()])
    r = auth_client.get(f"/api/mail/messages/{row.id}/attachments/0")
    assert r.status_code == 422
    assert (
        r.json()["detail"]
        == "This attachment can't be read. Download the .eml to open it elsewhere."
    )


def test_missing_file_on_attachment_and_eml_and_logged_out(
    auth_client, client, app, db, make_account
):
    acc = make_account()
    row = _keep(
        app, db, acc, "m1", make_mail("Gone", attachments=(("f.pdf", "application/pdf", MINI_PDF),))
    )
    eml_path(app.state.layout, acc.email, row.file_path).unlink()
    for tail in ("attachments/0", "eml"):
        r = auth_client.get(f"/api/mail/messages/{row.id}/{tail}")
        assert r.status_code == 404 and "missing" in r.json()["detail"]
    thread = auth_client.get("/api/mail/threads/t1", params={"account_id": acc.id}).json()
    assert thread[0]["file_missing"] is True
    auth_client.post("/api/auth/logout")
    for url in (
        f"/api/mail/messages/{row.id}",
        f"/api/mail/messages/{row.id}/eml",
        f"/api/mail/messages/{row.id}/attachments/0",
        "/api/mail/threads/t1?account_id=1",
    ):
        assert client.get(url).status_code == 401


def test_text_uses_the_charset_it_names_and_ignores_a_bad_one():
    from app.services.mail.reader import safe_charset, served_type

    assert served_type("text/plain", safe_charset("iso-8859-1")) == "text/plain; charset=iso-8859-1"
    assert served_type("text/plain", safe_charset("nope\r\nx")) == "text/plain; charset=utf-8"
    assert served_type("text/html") == "text/plain; charset=utf-8"


def test_only_real_text_encodings_are_kept():
    from app.services.mail.reader import safe_charset

    assert safe_charset("utf-8") == "utf-8" and safe_charset("windows-1252") == "windows-1252"
    for name in ("utf-7", "UTF-7", "base64", "rot13", "hex", "zlib", "bz2", "uu", "quopri"):
        assert safe_charset(name) is None, name


def test_a_mail_whose_images_cannot_be_checked_still_opens(
    auth_client, app, db, make_account, monkeypatch, caplog
):
    from app.api import mail_reader

    acc = make_account()
    row = _keep(app, db, acc, "m1", make_mail("Odd", html="<p>Hi</p>"))

    def broken(raw):
        raise RuntimeError("cleaner fell over")

    monkeypatch.setattr(mail_reader, "has_remote_images", broken)
    with caplog.at_level("WARNING"):
        r = auth_client.get(f"/api/mail/messages/{row.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_remote_images"] is False and body["file_missing"] is False
    assert any("remote images" in rec.getMessage() for rec in caplog.records)
