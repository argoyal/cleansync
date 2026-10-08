from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class JobOut(BaseModel):
    id: int
    pair_id: int
    kind: str
    trigger: str
    state: str
    started_at: UtcDatetime | None
    completed_at: UtcDatetime | None
    duration_s: float | None
    exit_code: int | None
    files_uploaded: int
    files_downloaded: int
    files_deleted_local: int
    files_deleted_remote: int
    files_changed: int
    conflicts: int
    planned_deletions: int
    bytes_transferred: int
    override_used: bool
    error_message: str | None


class ConflictOut(BaseModel):
    id: int
    pair_id: int
    path: str
    local_variant: str
    remote_variant: str
    local_size: int | None
    local_mtime: UtcDatetime | None
    remote_size: int | None
    remote_mtime: UtcDatetime | None
    state: str
    resolution: str | None


class ResolvedOut(ConflictOut):
    # "queued"/"dirty"/"already_queued", or "busy" when the follow-up sync will run later
    sync: str | None = None
