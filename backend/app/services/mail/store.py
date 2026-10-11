"""Mail on disk (spec §3.3): mail/<email>/<YYYY>/<MM>/<gmail-id>.eml, written whole or not at
all; and what CleanSync reads back out of a .eml (headers, attachments, text for search)."""

import hashlib
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from app.core.clock import as_utc
from app.core.pathsafe import safe_join
from app.paths import Layout
from app.services.mail.gmail import valid_mail_id

TMP_PREFIX = ".tmp-"
MAX_BODY_CHARS = 200_000
MISSING_ROOT_MSG = (
    "The mail folder on the storage disk is missing (is the disk connected?), "
    "so nothing was written."
)


class MailStoreError(Exception):
    pass


class MailRootMissing(MailStoreError):
    pass


def eml_rel(provider_id: str, when: datetime) -> str:
    if valid_mail_id(provider_id) is None:
        raise MailStoreError("not a usable mail id")
    d = as_utc(when)
    return f"{d:%Y}/{d:%m}/{provider_id}.eml"


def eml_path(layout: Layout, email: str, rel: str) -> Path:
    return safe_join(layout.mail_dir(email), rel)


def write_eml(layout: Layout, email: str, rel: str, raw: bytes) -> tuple[int, str]:
    """Write to a temporary name in the same folder, flush to disk, then rename, so a file
    exists only when complete. Folders are made only below mail/<email>/: when mail/ itself
    is missing (the disk dropped out), nothing is written at all."""
    if not layout.mail_root.is_dir():
        raise MailRootMissing(MISSING_ROOT_MSG)
    try:
        layout.mail_dir(email).mkdir(exist_ok=True)  # no parents: never makes mail/ itself
    except FileNotFoundError:
        raise MailRootMissing(MISSING_ROOT_MSG) from None
    target = eml_path(layout, email, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{TMP_PREFIX}{secrets.token_hex(6)}-{target.name}")
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)  # only there when something failed before the rename
    return len(raw), hashlib.sha256(raw).hexdigest()


def read_message(raw: bytes) -> EmailMessage:
    return BytesParser(policy=policy.default).parsebytes(raw)  # type: ignore[return-value]


def _header(msg: Message, name: str) -> str:
    try:
        value = msg.get(name)
        return "" if value is None else " ".join(str(value).split())
    except Exception:  # a header the mail library can't decode: leave it out
        return ""


def check_eml(path: Path, size: int, sha256: str) -> str | None:
    """None when the file here is the one that was downloaded; otherwise why it isn't."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return "the file is missing"
    except OSError:
        return "the file can't be read"
    if len(raw) != size:
        return "the file's size changed"
    if hashlib.sha256(raw).hexdigest() != sha256:
        return "the file's contents changed"
    try:
        msg = read_message(raw)
    except Exception:
        return "the file doesn't read as a mail"
    if not any(_header(msg, h) for h in ("From", "Date", "Message-ID", "Subject")):
        return "the file doesn't read as a mail"
    return None


def part_filename(part: Message) -> str:
    try:
        return part.get_filename() or ""
    except Exception:
        return ""


def attachment_parts(msg: Message) -> list[Message]:
    """The mail's attachments in a fixed order (the reader serves them by this position).
    Inline images (shown inside the mail) are not attachments."""
    out: list[Message] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        try:
            disp = part.get_content_disposition()
        except Exception:
            disp = None
        if disp == "attachment" or (part_filename(part) and disp != "inline"):
            out.append(part)
    return out


def _payload_size(part: Message) -> int:
    try:
        return len(part.get_payload(decode=True) or b"")
    except Exception:
        return 0


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: S110  odd HTML: keep what was read
        pass
    return " ".join(" ".join(p.out).split())


def _body_text(msg: EmailMessage) -> str:
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        content = part.get_content() if part is not None else ""
    except Exception:
        return ""
    if not isinstance(content, str):
        return ""
    text = html_to_text(content) if part.get_content_type() == "text/html" else content
    return text[:MAX_BODY_CHARS]


@dataclass
class ParsedMail:
    message_id: str | None = None
    from_addr: str = ""
    to_addrs: str = ""
    cc_addrs: str = ""
    subject: str = ""
    attachments: list[dict] = field(default_factory=list)
    body_text: str = ""


def parse_eml(raw: bytes) -> ParsedMail:
    """What the index keeps of a mail. Never raises: a broken mail gives empty fields."""
    try:
        msg = read_message(raw)
    except Exception:
        return ParsedMail()
    atts = [
        {
            "name": part_filename(p) or "attachment",
            "type": p.get_content_type(),
            "size": _payload_size(p),
        }
        for p in attachment_parts(msg)
    ]
    return ParsedMail(
        message_id=_header(msg, "Message-ID") or None,
        from_addr=_header(msg, "From"),
        to_addrs=_header(msg, "To"),
        cc_addrs=_header(msg, "Cc"),
        subject=_header(msg, "Subject"),
        attachments=atts,
        body_text=_body_text(msg),
    )


def sweep_temp_files(layout: Layout) -> int:
    """At startup: remove temporary files a crash left half-written."""
    if not layout.mail_root.is_dir():
        return 0
    gone = 0
    for p in layout.mail_root.rglob(f"{TMP_PREFIX}*"):
        try:
            p.unlink()
            gone += 1
        except OSError:
            pass
    return gone
