from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime


class CatalogOut(BaseModel):
    pair_id: int
    refreshed_at: UtcDatetime | None
    refreshing: bool
    error: str | None
    files: int
    folders: int
    bytes: int
    native_docs: int


class FileItemOut(BaseModel):
    name: str
    path: str  # account path, '/'-separated
    is_dir: bool
    size: int | None
    mtime: UtcDatetime | None
    # synced | syncing | upload | download | conflict | native | unsynced | unknown
    # ("offloaded" is reserved for a later version)
    status: str
    local: bool
    remote: bool
    mime: str | None
    native: str | None  # document | spreadsheet | presentation | drawing | form | site | other
    google_url: str | None
    starred: bool
    pair_id: int | None
    thumb: bool


class PairBrief(BaseModel):
    id: int
    name: str
    status: str
    initialized: bool
    local_subpath: str


class BrowseOut(BaseModel):
    account_id: int
    path: str
    pair: PairBrief | None
    items: list[FileItemOut]


class DriveQuota(BaseModel):
    total: int | None
    used: int | None
    trash: int | None
    checked_at: UtcDatetime | None


class DiskUsage(BaseModel):
    total: int
    used: int
    free: int


class StorageOut(BaseModel):
    account_id: int
    email: str
    drive: DriveQuota
    disk: DiskUsage
    synced_bytes: int | None  # bytes on disk inside this account's sync pairs


class MkdirIn(BaseModel):
    account_id: int
    path: str = ""  # the folder to create it in
    name: str = Field(max_length=1024)


class RenameIn(BaseModel):
    account_id: int
    path: str
    new_name: str = Field(max_length=1024)


class MoveIn(BaseModel):
    account_id: int
    paths: list[str] = Field(min_length=1, max_length=1000)
    to: str = ""


class DeleteIn(BaseModel):
    account_id: int
    paths: list[str] = Field(min_length=1, max_length=1000)


class OpOut(BaseModel):
    paths: list[str]
    # pair id -> what happened to the follow-up sync ("queued", "dirty", "not_initialized", ...)
    syncs: dict[int, str]


class UploadOut(BaseModel):
    path: str
    size: int
    syncs: dict[int, str]


class NewFileIn(BaseModel):
    account_id: int
    path: str = ""  # the folder to create it in
    # docx/xlsx/pptx: an Office file made here, then synced.
    # document/spreadsheet/presentation: a Google-native file made on Drive.
    kind: Literal["docx", "xlsx", "pptx", "document", "spreadsheet", "presentation"]
    name: str | None = Field(default=None, max_length=255)


class NewFileOut(BaseModel):
    path: str | None  # account path of a new Office file
    google_url: str | None  # where a new Google file opens
    syncs: dict[int, str]
