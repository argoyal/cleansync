from typing import Literal

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class SenderOut(BaseModel):
    address: str
    name: str
    count: int
    read_pct: int
    inbox_pct: int
    last_at: UtcDatetime
    bytes: int
    status: str | None
    method: str | None
    note: str | None
    newest_unsubscribe: str
    newest_unsubscribe_post: str | None
    newest_mail_id: int


class ScanOut(BaseModel):
    scanned: int
    total: int


class SendersOut(BaseModel):
    items: list[SenderOut]
    total: int
    suggested: int
    scan: ScanOut


class MarkIn(BaseModel):
    account_id: int
    addresses: list[str]
    status: Literal["unsubscribed"] | None


class ChangedOut(BaseModel):
    changed: int


class PickAllIn(BaseModel):
    tab: str = "all"
    q: str = ""


class JobIn(BaseModel):
    account_id: int
    kind: Literal["unsubscribe", "auto_archive", "keep"]
    addresses: list[str] | None = None
    all: PickAllIn | None = None


class JobStartedOut(BaseModel):
    job_id: int


class JobItemOut(BaseModel):
    address: str
    outcome: str | None
    method: str | None
    note: str | None
    link: str | None


class JobOut(BaseModel):
    id: int
    account_id: int
    kind: str
    state: str
    total: int
    done: int
    stop_asked: bool
    reason: str | None
    created_at: UtcDatetime
    finished_at: UtcDatetime | None
    items: list[JobItemOut]
