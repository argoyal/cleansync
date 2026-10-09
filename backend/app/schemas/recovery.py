from datetime import date

from pydantic import BaseModel, Field


class RecoveryEntryOut(BaseModel):
    id: str
    kind: str  # sync_backup | deleted | first_sync | resync | conflict | offload
    pair_id: int | None
    pair_name: str | None
    name: str
    original_path: str | None
    size: int | None
    day: date
    expires_on: date | None
    restorable: bool
    offload_id: int | None
    google_emptied: bool  # an offload past Google's 30-day trash: no longer in Drive's trash


class RecoveryListOut(BaseModel):
    total: int
    entries: list[RecoveryEntryOut]


class RestoreIn(BaseModel):
    account_id: int
    ids: list[str] = Field(min_length=1, max_length=1000)


class RestoreResultOut(BaseModel):
    id: str
    restored_to: str | None
    error: str | None


class RestoreOut(BaseModel):
    results: list[RestoreResultOut]
    syncs: dict[int, str]


class ZipIn(BaseModel):
    account_id: int
    # The count limit is checked by the route, for a plain message.
    ids: list[str] = Field(min_length=1)


class ZipOut(BaseModel):
    token: str
