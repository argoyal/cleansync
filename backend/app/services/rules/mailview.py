"""What a model is shown of one mail (AI rules spec §5), read only from the local copy: the
.eml is checked against its row first, so a changed or missing file is never sorted."""

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import getaddresses, parseaddr

from app.models import MailMessage
from app.paths import Layout
from app.services.mail.store import check_eml_bytes, eml_path, parse_eml, read_message

BODY_CHARS = 2000
_QUOTE_START = re.compile(
    r"^\s*(On\s.{1,300}\swrote:|-{2,}\s*Original Message\s*-{2,})\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_LINK = re.compile(r"https?://([^/\s<>\"')\]]+)[^\s<>\"')\]]*", re.IGNORECASE)


class CopyUnreadable(Exception):
    pass


@dataclass
class MailForModel:
    message_id: int
    sender: str
    sender_name: str
    to: list[str]
    cc: list[str]
    owner_in: str
    subject: str
    date: datetime
    attachments: list[str]
    list_unsubscribe: bool
    mailing_list: bool
    calendar_invite: bool
    text: str
    labels: list[str]


def sender_of(from_addr: str) -> str:
    addr = parseaddr(from_addr or "")[1].strip().lower()
    return addr if "@" in addr else ""


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[1].lower() if "@" in address else ""


def _addresses(value: str) -> list[str]:
    return [a.strip().lower() for _, a in getaddresses([value or ""]) if "@" in a]


def model_text(text: str, limit: int = BODY_CHARS) -> str:
    m = _QUOTE_START.search(text or "")
    if m:
        text = text[: m.start()]
    lines = [ln for ln in (text or "").splitlines() if not ln.lstrip().startswith(">")]
    text = _LINK.sub(lambda mm: f"[link: {mm.group(1).lower()}]", "\n".join(lines))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text[:limit]


def mail_for_model(layout: Layout, owner_email: str, row: MailMessage) -> MailForModel:
    path = eml_path(layout, owner_email, row.file_path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise CopyUnreadable("the file is missing") from None
    except OSError:
        raise CopyUnreadable("the file can't be read") from None
    problem = check_eml_bytes(raw, row.file_size, row.sha256)
    if problem:
        raise CopyUnreadable(problem)
    msg = read_message(raw)
    parsed = parse_eml(raw)
    to, cc = _addresses(row.to_addrs), _addresses(row.cc_addrs)
    owner = owner_email.lower()
    names = [str(a.get("name") or "") for a in (row.attachments or [])]
    calendar = any(p.get_content_type() == "text/calendar" for p in msg.walk()) or any(
        n.lower().endswith(".ics") for n in names
    )
    return MailForModel(
        message_id=row.id,
        sender=sender_of(row.from_addr),
        sender_name=parseaddr(row.from_addr or "")[0].strip(),
        to=to,
        cc=cc,
        owner_in="to" if owner in to else "cc" if owner in cc else "other",
        subject=row.subject or "",
        date=row.date,
        attachments=names,
        list_unsubscribe=bool(msg.get("List-Unsubscribe")),
        mailing_list=bool(msg.get("List-Id")) or bool(msg.get("List-Unsubscribe")),
        calendar_invite=calendar,
        text=model_text(parsed.body_text),
        labels=list(row.labels or []),
    )
