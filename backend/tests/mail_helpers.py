"""Test helpers: the real GmailAdapter talking to the fake Gmail, with no budget or pace, and
a Box with one account, a fake Gmail and a MailBackup to run against it."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.devtools.fake_gmail import FakeGmail, make_mail
from app.models import MailMessage, MailSyncState
from app.models.enums import MailJobKind
from app.services.events.bus import EventBus
from app.services.google.client import GoogleClient
from app.services.mail.backup import GB, BackupRun, MailBackup
from app.services.mail.gmail import GmailAdapter, StaticTokens
from app.services.mail.jobs import start_mail_job
from app.services.mail.store import eml_path


async def _no_sleep(_s):
    return None


def fake_adapter(fake: FakeGmail, *, page_size: int = 500, sleep=None, budget=None) -> GmailAdapter:
    google = GoogleClient(
        budget=budget,
        pacer=None,
        http_factory=lambda: httpx.AsyncClient(transport=fake.transport()),
        sleep=sleep or _no_sleep,
    )
    return GmailAdapter(google, StaticTokens("ya29.fake"), "gd_alice", page_size=page_size)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


class Box:
    """One account with mail set up, a fake Gmail, and a MailBackup to run against it."""

    def __init__(self, session_factory, layout, account, free=100 * GB):
        self.sf, self.layout, self.acc = session_factory, layout, account
        self.fake = FakeGmail(account.email)
        self.clock = Clock()
        self.free = free
        self.bus = EventBus()
        self.backup = MailBackup(
            layout=layout,
            session_factory=session_factory,
            bus=self.bus,
            clock=self.clock,
            disk_free=lambda _p: self.free,
        )
        self.budget = None

    def add(self, n, labels=("INBOX",), days_ago=10):
        when = self.clock.now - timedelta(days=days_ago)
        return [
            self.fake.add(make_mail(f"Mail {i}", when=when), labels=labels, when=when)
            for i in range(n)
        ]

    async def run(self, *, page_size=2, stop_after_pages=None):
        calls = {"n": 0}

        async def is_stopped():
            calls["n"] += 1
            return stop_after_pages is not None and calls["n"] > stop_after_pages

        with self.sf() as db:
            job = start_mail_job(
                db, self.layout, self.bus, self.acc.id, MailJobKind.BACKUP, "MANUAL"
            )
        r = BackupRun(
            account_id=self.acc.id,
            email=self.acc.email,
            adapter=fake_adapter(self.fake, page_size=page_size, budget=self.budget),
            job_id=job.id,
            floor_bytes=2 * GB,
            is_stopped=is_stopped,
            log_path=job.log_path,
        )
        state, message = await self.backup.run(r)
        return state, message, r

    def rows(self):
        with self.sf() as db:
            return list(
                db.scalars(select(MailMessage).where(MailMessage.account_id == self.acc.id))
            )

    def state(self):
        with self.sf() as db:
            return db.get(MailSyncState, self.acc.id)

    def fetches(self):
        return [
            c
            for c in self.fake.calls
            if c[0] == "GET" and "/messages/" in c[1] and c[2].get("format") == "raw"
        ]


def every_row_has_its_file(box):
    for row in box.rows():
        assert eml_path(box.layout, box.acc.email, row.file_path).is_file(), row.provider_id
