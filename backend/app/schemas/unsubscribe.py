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
