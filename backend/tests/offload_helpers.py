"""A backed-up account on a fake Gmail, with the mail queue stubbed so tests run jobs in order."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.devtools.fake_gmail import FakeGmail, make_mail
from app.models import MailMessage
from app.models.enums import ServiceKind, ServiceState
from app.services.accounts.services import ensure_services, set_service_state
from tests.mail_helpers import fake_adapter
from tests.test_mail_api import OneAdapter

NOW = datetime.now(UTC)
# subject -> (days ago, labels)
MAILBOX = {
    "Old statement": (400, ("INBOX", "Label_2")),
    "Old photos": (500, ("INBOX",)),
    "Ancient note": (900, ("INBOX",)),
    "Last month": (40, ("INBOX",)),
    "Last week": (7, ("INBOX",)),
    "Yesterday": (1, ("INBOX",)),
}


class RecordingQueue:
    def __init__(self, mail):
        self.mail = mail
        self.requests: list[tuple[int, str, dict, str]] = []

    def request(self, account_id, action, args=None, trigger="SCHEDULE"):
        item = (account_id, action, dict(args or {}), trigger)
        if item[:3] in [r[:3] for r in self.requests]:
            return "already_queued"
        self.requests.append(item)
        return "queued"

    def is_busy(self, account_id):
        return False

    def is_running(self, account_id):
        return False

    async def stop(self):
        pass


class OffloadBox:
    def __init__(self, app, acc, fake):
        self.app, self.acc, self.fake = app, acc, fake
        self.mail = app.state.mail
        self.offloads = app.state.mail_offloads
        self.queue = RecordingQueue(self.mail)
        self.mail.queue = self.queue

    async def backup(self):
        await self.mail.handle(self.acc.id, "backup", {}, "MANUAL")

    async def run_queued(self):
        while self.queue.requests:
            account_id, action, args, trigger = self.queue.requests.pop(0)
            await self.mail.handle(account_id, action, args, trigger)

    def row(self, subject) -> MailMessage:
        with self.app.state.session_factory() as db:
            return db.scalar(select(MailMessage).where(MailMessage.subject == subject))

    def provider_id(self, subject) -> str:
        return self.row(subject).provider_id


@pytest.fixture
def offload_box(app, make_account, db, stub_queue):
    acc = make_account()
    ensure_services(db, acc)
    set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    fake = FakeGmail(acc.email)
    fake.add_label("Work")
    fake.add_label("Receipts")
    for subject, (days, labels) in MAILBOX.items():
        when = NOW - timedelta(days=days)
        fake.add(make_mail(subject, when=when), labels=labels, when=when)
    app.state.mail.adapters = OneAdapter(fake_adapter(fake))
    return OffloadBox(app, acc, fake)
