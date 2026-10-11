from datetime import date

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime


class AccountIn(BaseModel):
    account_id: int


class QueuedOut(BaseModel):
    queued: str


class SetupInfoOut(BaseModel):
    total: int
    size_bytes: int | None


class MailFailureOut(BaseModel):
    provider_id: str
    attempts: int
    last_error: str


class MailJobBrief(BaseModel):
    id: int
    kind: str
    state: str
    started_at: UtcDatetime
    completed_at: UtcDatetime | None
    done: int
    message: str | None


class MailStatusOut(BaseModel):
    account_id: int
    email: str
    service: str
    phase: str | None
    stored: int
    stored_bytes: int
    total: int | None
    running: bool
    waiting: bool
    time_left_s: int | None
    last_run_at: UtcDatetime | None
    last_error: str | None
    job: MailJobBrief | None
    failed: list[MailFailureOut]
    budget_reached: bool


class ActivityJobOut(BaseModel):
    source: str  # files | mail
    id: int
    account_id: int
    title: str
    kind: str
    state: str
    started_at: UtcDatetime | None
    completed_at: UtcDatetime | None
    message: str | None
    log_url: str


class ThreadOut(BaseModel):
    thread_id: str
    count: int
    last_date: UtcDatetime
    message_id: int
    from_addr: str
    subject: str
    snippet: str
    has_attachments: bool
    unread: bool
    offloaded: bool
    deleted: bool


class ThreadPageOut(BaseModel):
    threads: list[ThreadOut]
    next_cursor: str | None
    total: int | None  # mails in this view or search (first page only)
    total_bytes: int | None


class LabelCountOut(BaseModel):
    id: str
    name: str
    count: int


class MailCountsOut(BaseModel):
    views: dict[str, int]
    labels: list[LabelCountOut]


class LabelOut(BaseModel):
    id: str
    name: str


class AttachmentOut(BaseModel):
    index: int
    name: str
    type: str
    size: int
    preview: bool


class MessageOut(BaseModel):
    id: int
    account_id: int
    provider_id: str
    thread_id: str
    from_addr: str
    to_addrs: str
    cc_addrs: str
    subject: str
    date: UtcDatetime
    size: int
    file_size: int
    labels: list[LabelOut]
    state: str
    offload_id: int | None
    attachments: list[AttachmentOut]
    has_remote_images: bool | None  # known for one opened mail; null in a thread list
    gmail_url: str | None
    file_missing: bool


class MailPickIn(BaseModel):
    account_id: int
    q: str | None = None
    message_ids: list[int] | None = Field(default=None, max_length=100_000)
    thread_ids: list[str] | None = Field(default=None, max_length=100_000)


class MailBriefOut(BaseModel):
    id: int
    subject: str
    from_addr: str
    date: UtcDatetime
    size: int


class ExcludedOut(BaseModel):
    reason: str
    count: int
    text: str


class OffloadPreviewOut(BaseModel):
    filter_text: str
    count: int
    bytes: int
    largest: list[MailBriefOut]
    oldest: list[MailBriefOut]
    excluded: list[ExcludedOut]


class OffloadStartIn(MailPickIn):
    confirm: int


class OffloadStartedOut(BaseModel):
    offload_id: int
    queued: str = "queued"


class MailOffloadOut(BaseModel):
    id: int
    account_id: int
    filter_text: str
    state: str
    total: int
    done: int
    skipped: int
    not_done: int
    bytes: int
    reason: str | None
    created_at: UtcDatetime
    finished_at: UtcDatetime | None
    trash_until: date  # Gmail empties its trash by itself after 30 days
    google_emptied: bool


class SkippedOut(BaseModel):
    message_id: int
    subject: str
    from_addr: str
    date: UtcDatetime
    reason: str


class MailOffloadDetailOut(MailOffloadOut):
    skipped_items: list[SkippedOut]
