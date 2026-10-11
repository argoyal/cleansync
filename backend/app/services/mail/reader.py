"""Reading a kept mail for display (spec §6.2): the cleaned body as a whole document,
inline images from the .eml, attachments by position."""

import base64
import html as html_lib
import logging
from email.message import EmailMessage
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MailLabel, MailMessage
from app.paths import Layout
from app.services.mail.sanitize import INLINE_LIMIT, Cleaned, cid_refs, sanitize_html
from app.services.mail.store import attachment_parts, eml_path, part_filename, read_message

log = logging.getLogger(__name__)
MISSING_FILE_MSG = "This mail's file is missing on the storage disk."
UNREADABLE_MSG = "This mail can't be shown. Download the .eml to open it elsewhere."
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


def inline_images(msg: EmailMessage, wanted: set[str] | None = None) -> dict[str, str]:
    """Content-ID → data: address, for images shown inside the mail (at most INLINE_LIMIT).
    With `wanted`, only the images the HTML points at are read out."""
    out: dict[str, str] = {}
    used = 0
    for part in msg.walk():
        cid = part.get("Content-ID")
        if part.is_multipart() or not cid or part.get_content_maintype() != "image":
            continue
        key = str(cid).strip().strip("<>")
        if wanted is not None and key not in wanted:
            continue
        try:
            data = part.get_payload(decode=True) or b""
        except (ValueError, LookupError):  # a broken image part: the mail shows without it
            continue
        used += len(data)
        if used > INLINE_LIMIT:
            break
        encoded = base64.b64encode(data).decode()
        out[key] = f"data:{part.get_content_type()};base64,{encoded}"
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


def render_body(raw: bytes, *, inline: bool = True) -> Rendered:
    """`inline=False` skips reading the inline images out (to only count remote ones)."""
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
        cleaned = sanitize_html(content, inline_images(msg, cid_refs(content)) if inline else {})
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
            "form-action 'none'",
            "base-uri 'none'",
            "frame-ancestors 'self'",
        ]
    )


PREVIEW_TYPES = ("application/pdf", "text/plain")


def can_preview(content_type: str) -> bool:
    """What the browser can show by itself (spec 6.2): PDF, images, plain text. SVG can carry
    script, so it is only ever downloaded."""
    if content_type == "image/svg+xml":
        return False
    return content_type in PREVIEW_TYPES or content_type.startswith("image/")


def attachment_of(raw: bytes, index: int) -> tuple[str, str, bytes]:
    parts = attachment_parts(read_message(raw))
    if index < 0 or index >= len(parts):
        raise IndexError(index)
    part = parts[index]
    try:
        data = part.get_payload(decode=True) or b""
    except Exception:
        data = b""
    return part_filename(part) or "attachment", part.get_content_type(), data


def gmail_url(email: str, provider_id: str) -> str:
    return (
        f"https://mail.google.com/mail/u/{quote(email, safe='')}/#all/{quote(provider_id, safe='')}"
    )


def label_names(db: Session, account_id: int, ids: list[str]) -> list[dict]:
    """User labels of a mail, by name (system labels are what the views show)."""
    rows = db.scalars(
        select(MailLabel).where(
            MailLabel.account_id == account_id,
            MailLabel.type == "user",
            MailLabel.provider_label_id.in_(ids),
        )
    )
    return sorted(
        ({"id": r.provider_label_id, "name": r.name} for r in rows), key=lambda x: x["name"].lower()
    )


def has_remote_images(raw: bytes) -> bool:
    """One cleaning pass that doesn't read the inline images out, so it costs little."""
    return render_body(raw, inline=False).remote_images > 0
