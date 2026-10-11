"""Reading a kept mail for display (spec §6.2): the cleaned body as a whole document,
inline images from the .eml, attachments by position."""

import base64
import html as html_lib
import logging
from email.message import EmailMessage

from app.models import MailMessage
from app.paths import Layout
from app.services.mail.sanitize import Cleaned, sanitize_html
from app.services.mail.store import eml_path, read_message

log = logging.getLogger(__name__)
MISSING_FILE_MSG = "This mail's file is missing on the storage disk."
UNREADABLE_MSG = "This mail can't be shown. Download the .eml to open it elsewhere."
INLINE_LIMIT = 10 * 1024 * 1024
# Mail HTML is written for white paper, so the mail frame keeps fixed paper colours: the
# stated exception to "colours are theme tokens" (pre-flight ruling P3 F7).
_STYLE = (
    "body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:16px;color:#1d2230;"
    "background:#fff;overflow-wrap:anywhere}img{max-width:100%;height:auto}"
    "pre{white-space:pre-wrap;font:inherit;margin:0}table{max-width:100%}"
)

Rendered = Cleaned  # the same shape: the cleaned page and how many remote images wait


class MailFileMissing(Exception):
    pass


def read_eml_of(layout: Layout, email: str, row: MailMessage) -> bytes:
    try:
        return eml_path(layout, email, row.file_path).read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        raise MailFileMissing(MISSING_FILE_MSG) from None


def inline_images(msg: EmailMessage) -> dict[str, str]:
    """Content-ID → data: address, for images shown inside the mail (at most 10 MB)."""
    out: dict[str, str] = {}
    used = 0
    for part in msg.walk():
        cid = part.get("Content-ID")
        if part.is_multipart() or not cid or part.get_content_maintype() != "image":
            continue
        try:
            data = part.get_payload(decode=True) or b""
        except (ValueError, LookupError):  # a broken image part: the mail shows without it
            continue
        used += len(data)
        if used > INLINE_LIMIT:
            break
        encoded = base64.b64encode(data).decode()
        out[str(cid).strip().strip("<>")] = f"data:{part.get_content_type()};base64,{encoded}"
    return out


def _document(body: str) -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8"><base target="_blank">'
        f"<style>{_STYLE}</style></head><body>{body}</body></html>"
    )


def _text_of(part: EmailMessage) -> str:
    try:
        return part.get_content()
    except LookupError:  # a character set Python doesn't know: read it as UTF-8
        data = part.get_payload(decode=True) or b""
        return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else ""


def render_body(raw: bytes) -> Rendered:
    try:
        msg = read_message(raw)
        part = msg.get_body(preferencelist=("html", "plain"))
        content = _text_of(part) if part is not None else ""
    except Exception:  # a mail the mail library can't take apart: say so instead of failing
        log.warning("could not read a mail body for display", exc_info=True)
        return Rendered(_document(f"<p>{UNREADABLE_MSG}</p>"), 0)
    if not isinstance(content, str):
        content = ""
    if part is not None and part.get_content_type() == "text/html":
        cleaned = sanitize_html(content, inline_images(msg))
        return Rendered(_document(cleaned.html), cleaned.remote_images)
    return Rendered(_document(f"<pre>{html_lib.escape(content)}</pre>"), 0)


def body_csp(images: bool) -> str:
    """The mail frame's own policy: no script at all, nothing loaded but inline images (and
    remote ones after "Show images"), shown only inside CleanSync's pages."""
    img = "img-src data:" + (" https: http:" if images else "")
    return "; ".join(
        [
            "sandbox allow-popups allow-popups-to-escape-sandbox",
            "default-src 'none'",
            img,
            "style-src 'unsafe-inline'",
            "font-src data:",
            "frame-ancestors 'self'",
        ]
    )
