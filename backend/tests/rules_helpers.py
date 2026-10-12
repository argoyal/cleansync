"""Keep a mail here exactly as the backup does (file first, then the row and search entry)."""

import uuid
from datetime import UTC, datetime

from app.services.mail.adapter import MailMeta
from app.services.mail.index import add_message
from app.services.mail.store import eml_rel, parse_eml, write_eml


def keep(db, layout, acc, raw: bytes, *, pid=None, labels=("INBOX",), when=None):
    when = when or datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    pid = pid or uuid.uuid4().hex[:16]
    rel = eml_rel(pid, when)
    size, sha = write_eml(layout, acc.email, rel, raw)
    meta = MailMeta(pid, pid, tuple(labels), when, len(raw), None, "")
    return add_message(db, acc.id, meta, parse_eml(raw), rel, size, sha, when)
