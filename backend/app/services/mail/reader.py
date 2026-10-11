"""Reading a kept mail for display (spec §6.2): the cleaned body as a whole document,
inline images from the .eml, attachments by position."""

import base64
import codecs
import html as html_lib
import logging
import re
from email.message import EmailMessage
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MailLabel, MailMessage
from app.paths import Layout
from app.services.files.mimes import serve_policy
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


PREVIEW_TYPES = (
    "application/pdf",
    "text/plain",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/avif",
)
OTHER_TYPE = "application/octet-stream"
_TYPE_OK = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")
_COMMENT = re.compile(r"\([^()]*\)")
MAX_NAME_CHARS = 200
UNREADABLE_ATTACHMENT_MSG = "This attachment can't be read. Download the .eml to open it elsewhere."


class AttachmentUnreadable(Exception):
    pass


def clean_type(content_type: str | None) -> str:
    """One plain `maintype/subtype` token: no parameters, comments, spaces or control
    characters. Anything that doesn't look like a type becomes a generic download."""
    text = (content_type or "").split(";", 1)[0]
    while True:  # comments can nest
        text = _COMMENT.sub("", text)
        if "(" not in text:
            break
        text = text.replace("(", "").replace(")", "")
    text = "".join(c for c in text if c.isprintable() and not c.isspace()).lower()
    return text if _TYPE_OK.fullmatch(text) else OTHER_TYPE


def can_preview(content_type: str) -> bool:
    """What the browser can show by itself (spec 6.2): PDF, plain text and the plain image
    formats. SVG can carry script, so it (like every other type) is only downloaded."""
    return clean_type(content_type) in PREVIEW_TYPES


def served_type(content_type: str, charset: str | None = None) -> str:
    """The Content-Type to send for an attachment."""
    ctype = clean_type(content_type)
    if ctype in PREVIEW_TYPES and ctype != "text/plain":
        return ctype
    if serve_policy(ctype)[0].startswith("text/plain"):
        return f"text/plain; charset={charset or 'utf-8'}"  # HTML etc. never runs on our address
    return OTHER_TYPE


def safe_charset(name: str | None) -> str | None:
    if not name:
        return None
    try:
        codecs.lookup(name)
    except LookupError:
        return None
    return name if re.fullmatch(r"[A-Za-z0-9_.:-]{1,40}", name) else None


def download_name(name: str) -> str:
    """A name safe to put in a header and to save under: no slashes, at most 200 characters,
    the extension kept."""
    name = "".join(c if c.isprintable() else "_" for c in name.replace("/", "_").replace("\\", "_"))
    name = name.strip() or "attachment"
    if len(name) <= MAX_NAME_CHARS:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or len(ext) > 20:
        return name[:MAX_NAME_CHARS]
    return stem[: MAX_NAME_CHARS - len(ext) - 1] + "." + ext


def open_attachment(raw: bytes, index: int) -> tuple[str, str, bytes, str | None]:
    """(name, clean type, data, the attachment's own character set)."""
    parts = attachment_parts(read_message(raw))
    if index < 0 or index >= len(parts):
        raise IndexError(index)
    part = parts[index]
    try:
        data = part.get_payload(decode=True)
        charset = safe_charset(part.get_content_charset())
    except Exception:
        log.warning("could not decode attachment %s of a mail", index, exc_info=True)
        raise AttachmentUnreadable(UNREADABLE_ATTACHMENT_MSG) from None
    if not isinstance(data, bytes):
        log.warning("attachment %s of a mail has no readable content", index)
        raise AttachmentUnreadable(UNREADABLE_ATTACHMENT_MSG)
    return part_filename(part) or "attachment", clean_type(part.get_content_type()), data, charset


def attachment_of(raw: bytes, index: int) -> tuple[str, str, bytes]:
    name, ctype, data, _ = open_attachment(raw, index)
    return name, ctype, data


def gmail_url(email: str, provider_id: str) -> str:
    return (
        f"https://mail.google.com/mail/u/{quote(email, safe='')}/#all/{quote(provider_id, safe='')}"
    )


def user_label_map(db: Session, account_id: int) -> dict[str, str]:
    """Every user label of the account, id to name, in one query."""
    rows = db.scalars(
        select(MailLabel).where(MailLabel.account_id == account_id, MailLabel.type == "user")
    )
    return {r.provider_label_id: r.name for r in rows}


def label_names(
    db: Session, account_id: int, ids: list[str], known: dict[str, str] | None = None
) -> list[dict]:
    """User labels of a mail, by name (system labels are what the views show)."""
    names = known if known is not None else user_label_map(db, account_id)
    return sorted(
        ({"id": i, "name": names[i]} for i in ids if i in names), key=lambda x: x["name"].lower()
    )


def has_remote_images(raw: bytes) -> bool:
    """One cleaning pass that doesn't read the inline images out, so it costs little."""
    return render_body(raw, inline=False).remote_images > 0
