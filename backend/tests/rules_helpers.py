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


from app.devtools.fake_gmail import FakeGmail, make_mail
from app.services.rules.apply import Applier
from tests.mail_helpers import fake_adapter


class GmailBox:
    """A kept mailbox whose mail is also in a fake Gmail under the same ids."""

    def __init__(self, session_factory, layout, account):
        self.sf, self.layout, self.acc = session_factory, layout, account
        self.fake = FakeGmail(account.email)
        self.adapter = fake_adapter(self.fake)
        self.applier = Applier(
            session_factory=session_factory, layout=layout, adapter_for=lambda _a: self.adapter
        )

    def add(self, subject, labels=("INBOX",), **mail_kw):
        raw = make_mail(subject, **mail_kw)
        pid = self.fake.add(raw, labels=labels)
        with self.sf() as db:
            return keep(db, self.layout, self.acc, raw, pid=pid, labels=labels)
