from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import Direction
from app.services.app_settings import INTERVAL_CHOICES, RETENTION_CHOICES


def _check_interval(v: int | None) -> int | None:
    if v not in INTERVAL_CHOICES:
        raise ValueError("interval must be 60, 300, 900, 1800, 3600 or null (manual)")
    return v


def _check_retention(v: int | None) -> int | None:
    if v not in RETENTION_CHOICES:
        raise ValueError("retention must be 7, 30, 90 or null (never delete)")
    return v


class PairCreate(BaseModel):
    account_id: int
    name: str = Field(min_length=1, max_length=128)
    local_subpath: str = ""
    remote_path: str = ""
    direction: Direction = Direction.BIDIRECTIONAL
    schedule_interval_s: int | None = None
    max_deletions: int | None = Field(default=None, ge=0)
    backups_enabled: bool | None = None
    backup_retention_days: int | None = None

    _iv = field_validator("schedule_interval_s")(_check_interval)
    _rv = field_validator("backup_retention_days")(_check_retention)


class PairUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    schedule_interval_s: int | None = None
    max_deletions: int | None = Field(default=None, ge=0)
    backups_enabled: bool | None = None
    backup_retention_days: int | None = None

    _iv = field_validator("schedule_interval_s")(_check_interval)
    _rv = field_validator("backup_retention_days")(_check_retention)


class PairOut(BaseModel):
    id: int
    account_id: int
    account_email: str
    name: str
    local_subpath: str
    local_path: str
    remote_path: str
    direction: str
    schedule_interval_s: int | None
    paused: bool
    max_deletions: int
    backups_enabled: bool
    backup_retention_days: int | None
    initialized: bool
    status: str
    attention_reason: str | None
    last_error: str | None
    last_sync_at: datetime | None
    last_success_at: datetime | None
    last_error_at: datetime | None
    next_sync_at: datetime | None
