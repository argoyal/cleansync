"""What CleanSync needs from a mail provider (spec §3.2). Gmail now; Outlook later."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

# Never backed up (spec §2): Spam, Trash, drafts and chats.
SKIP_LABELS = frozenset({"SPAM", "TRASH", "DRAFT", "CHAT"})
IN_INBOX = "in_inbox"  # in the mailbox, not in its trash
IN_TRASH = "in_trash"
GONE = "gone"


@dataclass(frozen=True)
class MailboxInfo:
    email: str
    total: int
    history_id: str


@dataclass(frozen=True)
class ProviderLabel:
    id: str
    name: str
    type: str  # system | user


@dataclass(frozen=True)
class MailMeta:
    id: str
    thread_id: str
    labels: tuple[str, ...]
    internal_date: datetime
    size: int
    history_id: str | None
    snippet: str = ""


@dataclass
class MailChanges:
    """What changed since a history id: new mail (with its labels now), label changes, and
    mail deleted for good. history_id is where the next check starts."""

    history_id: str
    added: dict[str, tuple[str, ...]] = field(default_factory=dict)
    deleted: set[str] = field(default_factory=set)
    labels: dict[str, tuple[str, ...]] = field(default_factory=dict)


class TooOld:
    """changes_since: the provider no longer has history that far back (Gmail's 404)."""


TOO_OLD = TooOld()


@dataclass(frozen=True)
class ProviderState:
    where: str  # IN_INBOX | IN_TRASH | GONE
    labels: tuple[str, ...]
    size: int | None


class MailApiError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class MailAuthError(MailApiError):
    """Reconnect needed. login: the whole login is gone (else only the mail permission)."""

    def __init__(self, message: str, *, login: bool) -> None:
        super().__init__(message)
        self.login = login


class MailNotFound(MailApiError):
    pass


class MailSetupProblem(MailApiError):
    """Something the owner must fix outside CleanSync (say, the Gmail API turned off for the
    Cloud project). Its message says how, so it is shown as it is."""


class MailUnreachable(MailApiError):
    """Trouble reaching Gmail at all, not a refusal of one mail: no network, the saved login
    could not be read, or Gmail's own servers failed (5xx)."""


class MailAdapter(Protocol):
    async def profile(self) -> MailboxInfo: ...

    async def list_page(self, cursor: str | None) -> tuple[list[str], str | None]: ...

    async def fetch_raw(self, id: str) -> tuple[bytes, MailMeta]: ...

    async def changes_since(self, history_id: str) -> MailChanges | TooOld: ...

    async def labels(self) -> list[ProviderLabel]: ...

    async def trash(self, id: str) -> None: ...

    async def untrash(self, id: str) -> None: ...

    async def state(self, id: str) -> ProviderState: ...

    async def delete_forever(self, id: str) -> None: ...

    async def import_raw(self, raw: bytes, labels: list[str], internal_date: datetime) -> str: ...

    async def find_by_message_id(self, message_id: str) -> list[str]: ...
