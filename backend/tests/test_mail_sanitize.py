from datetime import UTC, datetime
from email.message import EmailMessage

import pytest

from app.devtools.fake_gmail import make_mail
from app.services.mail import index, reader
from app.services.mail.adapter import MailMeta
from app.services.mail.reader import INLINE_LIMIT, UNREADABLE_MSG, body_csp, render_body
from app.services.mail.sanitize import sanitize_html
from app.services.mail.store import parse_eml, write_eml

NOW = datetime(2026, 10, 10, tzinfo=UTC)

HOSTILE = (
    '<p onclick="steal()">Hello <b>there</b></p><script>alert(1)</script>'
    '<a href="javascript:alert(1)">bad</a><a href="https://example.com/x">good</a>'
    '<a href="data:text/html,hi">data link</a>'
    '<iframe src="https://evil.example.com"></iframe><form action="https://evil.example.com"><input name="pw"></form>'
    '<object data="x.swf"></object><link rel="stylesheet" href="https://evil.example.com/a.css">'
    '<style>p > b { color: red }</style><img src="javascript:alert(1)">'
)


def test_scripts_handlers_and_dangerous_links_are_removed():
    out = sanitize_html(HOSTILE, {})
    html = out.html
    for gone in (
        "script",
        "alert",
        "onclick",
        "steal",
        "javascript:",
        "iframe",
        "<form",
        "<input",
        "<object",
        "<link",
        "data:text",
    ):
        assert gone not in html, gone
    assert "<b>there</b>" in html
    assert 'href="https://example.com/x"' in html and 'rel="noopener noreferrer"' in html
    assert "<style>p > b { color: red }</style>" in html  # CSS can't run script
    assert out.remote_images == 0


@pytest.mark.parametrize(
    "hostile",
    [
        '<svg onload="alert(1)"><script>alert(1)</script><circle r="1"/></svg>',
        "<math><mtext><script>alert(1)</script></mtext></math>",
        '<img src="x" onerror="alert(1)">',
        '<body onload="alert(1)">x</body>',
        '<a href="JaVaScRiPt:alert(1)">x</a>',
        '<a href=" javascript:alert(1)">x</a>',
        '<a href="java\tscript:alert(1)">x</a>',
        '<a href="&#106;avascript:alert(1)">x</a>',
        '<a href="vbscript:alert(1)">x</a>',
        '<embed src="https://evil.example.com/x.swf">',
        '<frameset><frame src="https://evil.example.com"></frameset>',
        "<style>p{color:red}</style><script>alert(1)</script>",
        "<style>p{color:red}</style ><script>alert(1)</script>",
        "<!--<script>alert(1)</script>-->",
        '<button formaction="javascript:alert(1)">go</button>',
        '<p style="x" onmouseover="alert(1)">hover</p>',
        "<template><script>alert(1)</script></template>",
        '<noscript><p title="</noscript><script>alert(1)</script>"></p></noscript>',
    ],
)
def test_no_way_to_run_script_survives(hostile):
    html = sanitize_html(hostile, {}).html.lower()
    for gone in ("<script", "alert", "javascript", "vbscript", "onload", "onerror", "<svg"):
        assert gone not in html, (gone, html)
    for gone in ("<embed", "<frame", "<math", "<button", "onmouseover", "<template"):
        assert gone not in html, (gone, html)


def test_a_mail_cannot_change_where_links_go_or_reload_the_page():
    out = sanitize_html(
        '<base href="https://evil.example.com/"><meta http-equiv="refresh" '
        'content="0;url=https://evil.example.com"><a href="/relative">x</a>'
        '<a href="mailto:bob@example.com">mail</a>',
        {},
    )
    assert "<base" not in out.html and "<meta" not in out.html and "refresh" not in out.html
    assert 'href="/relative"' not in out.html  # only http, https and mailto links stay
    assert 'href="mailto:bob@example.com"' in out.html
    page = render_body(make_mail("Base", html='<base href="https://evil.example.com/">hi'))
    assert page.html.count("<base") == 1 and '<base target="_blank">' in page.html


def test_styles_stay_text_and_the_frame_policy_stops_what_they_could_load():
    css = (
        "<style>@import url(https://evil.example.com/a.css);"
        "p{width:expression(alert(1));background:url(https://track.example.com/b.gif)}"
        "</style><p style=\"background:url('https://track.example.com/c.gif')\">x</p>"
    )
    out = sanitize_html(css, {})
    assert "<style>" in out.html  # kept as text: no browser runs expression() any more
    assert out.remote_images == 2  # the two CSS backgrounds; @import is a style sheet
    for images in (False, True):
        policy = body_csp(images)
        # @import and fonts need a style or font source; only inline styles and data: fonts are allowed
        assert "style-src 'unsafe-inline'" in policy.split("; ")
        assert "font-src data:" in policy.split("; ")
        assert "default-src 'none'" in policy.split("; ")
        assert "script-src" not in policy and "allow-scripts" not in policy
        assert "allow-same-origin" not in policy and "allow-forms" not in policy
        assert "form-action 'none'" in policy.split("; ")
        assert "base-uri 'none'" in policy.split("; ")
    # background images in CSS fall under img-src: blocked until "Show images"
    assert "img-src data:" in body_csp(False).split("; ")


def test_images_only_keep_safe_addresses():
    out = sanitize_html(
        '<img src="data:image/png;base64,AAAA"><img src="data:text/html,<script>">'
        '<img src="file:///etc/passwd"><img src="//cdn.example.com/a.png">'
        '<img src="cid:nope"><img srcset="https://track.example.com/a.png 2x">',
        {},
    )
    assert 'src="data:image/png;base64,AAAA"' in out.html
    for gone in ("data:text", "file:", "cdn.example.com", "cid:", "srcset", "track"):
        assert gone not in out.html, gone
    assert out.remote_images == 0


def test_remote_images_wait_for_show_images():
    out = sanitize_html(
        '<img src="https://track.example.com/p.gif" alt="t"><img src="http://x.example.com/a.png">',
        {},
    )
    assert out.remote_images == 2
    assert (
        'src="https://track.example.com/p.gif"' in out.html
    )  # kept, but the frame's policy blocks it
    assert "img-src data:" in body_csp(False).split("; ")
    assert "img-src data: https: http:" in body_csp(True)
    for part in (
        "sandbox allow-popups allow-popups-to-escape-sandbox",
        "default-src 'none'",
        "frame-ancestors 'self'",
    ):
        assert part in body_csp(False)
    assert "script-src" not in body_csp(True)


def test_inline_images_come_from_the_mail_itself():
    raw = make_mail(
        "Photo",
        html='<p>Look</p><img src="cid:photo1" alt="p"><img src="cid:missing">',
        inline_png_cid="photo1",
    )
    page = render_body(raw)
    assert "data:image/png;base64," in page.html
    assert "cid:" not in page.html
    assert page.remote_images == 0
    assert page.html.startswith("<!doctype html>")
    assert '<base target="_blank">' in page.html


def test_remote_images_in_css_also_wait_for_show_images():
    out = sanitize_html(
        '<style>td{background:URL( "https://track.example.com/a.gif" )}</style>'
        "<table><tr><td style=\"background-image:url('http://x.example.com/b.png')\">x</td>"
        "</tr></table>"
        '<p style="background:url(data:image/png;base64,AAAA)">inline</p>'
        "<p>Read more at url(https://example.com) in the text</p>",
        {},
    )
    assert out.remote_images == 2


def test_one_image_repeated_many_times_stays_under_the_limit():
    big = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 800  # about 200 KB
    msg = EmailMessage()
    msg["From"] = "friend@example.com"
    msg["Subject"] = "Repeats"
    msg.set_content("x")
    msg.add_alternative("<p>x</p>" + '<img src="cid:big">' * 500, subtype="html")
    msg.get_payload()[1].add_related(big, "image", "png", cid="<big>", disposition="inline")
    page = render_body(msg.as_bytes())
    shown = page.html.count("data:image/png;base64,")
    assert 0 < shown < 500  # later references are dropped once the limit is reached
    assert page.html.count("<img") == 500
    assert len(page.html) < INLINE_LIMIT + 100_000


def test_only_the_images_the_mail_points_at_are_read_out():
    raw = make_mail("Photo", html='<p>Look</p><img src="cid:photo1">', inline_png_cid="photo1")
    msg = reader.read_message(raw)
    assert set(reader.inline_images(msg, set())) == set()
    assert set(reader.inline_images(msg, {"photo1"})) == {"photo1"}


def test_plain_text_mail_is_shown_as_text_never_as_html():
    raw = make_mail("Text", text="Line one\n<script>alert(1)</script> & more")
    page = render_body(raw)
    assert "&lt;script&gt;" in page.html and "<script>" not in page.html


def test_a_mail_in_an_unknown_character_set_is_still_shown_and_still_cleaned():
    raw = (
        b"From: friend@example.com\r\nSubject: Odd\r\n"
        b"Content-Type: text/html; charset=x-nonsense\r\n\r\n"
        b"<p>Hello</p><script>alert(1)</script>\r\n"
    )
    page = render_body(raw)
    assert "<p>Hello</p>" in page.html and "alert" not in page.html


def test_a_mail_that_cannot_be_read_says_so(monkeypatch):
    def broken(raw):
        raise ValueError("not a mail")

    monkeypatch.setattr(reader, "read_message", broken)
    page = render_body(make_mail("Broken", html="<p>x</p>"))
    assert page.html.startswith("<!doctype html>") and UNREADABLE_MSG in page.html
    assert page.remote_images == 0


def test_the_body_route_sends_the_cleaned_mail_with_its_own_policy(
    auth_client, app, db, make_account
):
    acc = make_account()
    raw = make_mail("Hostile", html=HOSTILE + '<img src="https://track.example.com/p.gif">')
    rel = "2026/10/m1.eml"
    size, sha = write_eml(app.state.layout, acc.email, rel, raw)
    row = index.add_message(
        db,
        acc.id,
        MailMeta("m1", "m1", ("INBOX",), NOW, size, "1", ""),
        parse_eml(raw),
        rel,
        size,
        sha,
        NOW,
    )
    r = auth_client.get(f"/api/mail/messages/{row.id}/body")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["content-security-policy"] == body_csp(False)
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["cache-control"] == "private, no-store"
    assert "alert" not in r.text and "<b>there</b>" in r.text
    shown = auth_client.get(f"/api/mail/messages/{row.id}/body?images=1")
    assert shown.headers["content-security-policy"] == body_csp(True)


def test_the_body_route_says_when_the_mail_or_its_file_is_missing(
    auth_client, app, db, make_account
):
    acc = make_account()
    raw = make_mail("Gone")
    row = index.add_message(
        db,
        acc.id,
        MailMeta("m2", "m2", ("INBOX",), NOW, len(raw), "1", ""),
        parse_eml(raw),
        "2026/10/m2.eml",
        len(raw),
        "0" * 64,
        NOW,
    )
    r = auth_client.get(f"/api/mail/messages/{row.id}/body")
    assert r.status_code == 404 and "missing" in r.json()["detail"]
    assert auth_client.get("/api/mail/messages/999999/body").status_code == 404


def test_the_body_route_needs_a_login(client):
    assert client.get("/api/mail/messages/1/body").status_code == 401
