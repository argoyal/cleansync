from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime


class OffloadIn(BaseModel):
    account_id: int
    paths: list[str] = Field(min_length=1, max_length=1000)  # account paths


class PreviewItemOut(BaseModel):
    path: str
    pair_id: int | None
    pair_name: str | None
    is_dir: bool
    file_count: int
    total_bytes: int
    stays_on_drive: list[str]
    blocked: str | None


class PreviewOut(BaseModel):
    items: list[PreviewItemOut]
    total_files: int  # of the items that can go
    total_bytes: int
    stopped: str | None  # why nothing can start right now (syncing stopped)


class BatchItemOut(BaseModel):
    path: str
    state: str  # waiting | running | done | attention | failed
    message: str | None
    offload_id: int | None


class BatchOut(BaseModel):
    id: str
    account_id: int
    done: bool
    items: list[BatchItemOut]


class OffloadOut(BaseModel):
    id: int
    pair_id: int
    pair_name: str
    account_id: int
    path: str  # account path
    is_dir: bool
    file_count: int
    total_bytes: int
    state: str
    trash_verified: bool
    trash_emptied: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime
    note: str | None
    busy: bool  # being emptied from Drive's trash right now
