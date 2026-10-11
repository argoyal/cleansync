from pydantic import BaseModel

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
