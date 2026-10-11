"""Which MailAdapter an account uses: Gmail for Google accounts; for a pretend local account
(development and CI only) the real GmailAdapter talking to a fake Gmail saved in
app/data/fake-gmail/<email>.json."""

from pathlib import Path

import httpx

from app.devtools.fake_gmail import FakeGmail
from app.models import Account
from app.models.enums import Provider
from app.paths import Layout
from app.services.accounts.service import is_pretend
from app.services.google.client import GoogleClient
from app.services.mail.adapter import MailAdapter
from app.services.mail.gmail import GmailAdapter, StaticTokens


class MailAdapters:
    def __init__(
        self, *, google: GoogleClient, tokens, layout: Layout, allow_pretend: bool
    ) -> None:
        self.google = google
        self.tokens = tokens
        self.layout = layout
        self.allow_pretend = allow_pretend
        self._fakes: dict[str, FakeGmail] = {}

    def fake_path(self, email: str) -> Path:
        return self.layout.data / "fake-gmail" / f"{email}.json"

    def for_account(self, account: Account) -> MailAdapter:
        if is_pretend(account, self.allow_pretend):
            fake = self._fakes.get(account.email)
            if fake is None:
                fake = FakeGmail.load(self.fake_path(account.email), email=account.email)
                self._fakes[account.email] = fake
            google = GoogleClient(
                budget=self.google.budget,
                pacer=self.google.pacer,
                http_factory=lambda: httpx.AsyncClient(transport=fake.transport()),
            )
            return GmailAdapter(google, StaticTokens("pretend"), account.remote_name)
        if account.provider != Provider.GOOGLE:
            raise LookupError(f"No mail for {account.provider} accounts yet.")
        return GmailAdapter(self.google, self.tokens, account.remote_name)
