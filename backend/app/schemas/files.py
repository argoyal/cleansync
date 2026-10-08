from pydantic import BaseModel

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
