from enum import StrEnum


class AccountStatus(StrEnum):
    CONNECTED = "CONNECTED"
    NEEDS_REAUTH = "NEEDS_REAUTH"
    DISCONNECTED = "DISCONNECTED"


class Direction(StrEnum):
    BIDIRECTIONAL = "BIDIRECTIONAL"
    REMOTE_TO_LOCAL = "REMOTE_TO_LOCAL"
    LOCAL_TO_REMOTE = "LOCAL_TO_REMOTE"


class PairStatus(StrEnum):
    NEW = "NEW"
    HEALTHY = "HEALTHY"
    RUNNING = "RUNNING"
    ATTENTION = "ATTENTION"
    ERROR = "ERROR"
    PAUSED = "PAUSED"


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class JobKind(StrEnum):
    PREVIEW = "PREVIEW"
    DRY_RUN = "DRY_RUN"
    BISYNC = "BISYNC"
    RESYNC = "RESYNC"
    SYNC = "SYNC"


class Trigger(StrEnum):
    SCHEDULE = "SCHEDULE"
    WATCHER = "WATCHER"
    MANUAL = "MANUAL"
    INITIAL = "INITIAL"
    RESOLVE = "RESOLVE"
    RECOVERY = "RECOVERY"
    RESYNC = "RESYNC"
    APP = "APP"  # a file action in the web UI (new folder, rename, move, delete)


class ConflictState(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    SKIPPED = "SKIPPED"


class AttentionReason(StrEnum):
    BLOCKED = "BLOCKED"
    CONFLICTS = "CONFLICTS"
    NEEDS_RESYNC = "NEEDS_RESYNC"
    ACCESS_CHECK = "ACCESS_CHECK"
    AUTH = "AUTH"
    FAILED = "FAILED"
    PLAN_UNREADABLE = "PLAN_UNREADABLE"
