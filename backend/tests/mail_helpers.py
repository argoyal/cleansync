"""Test helpers: the real GmailAdapter talking to the fake Gmail, with no budget or pace."""

import httpx

from app.devtools.fake_gmail import FakeGmail
from app.services.google.client import GoogleClient
from app.services.mail.gmail import GmailAdapter, StaticTokens


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
