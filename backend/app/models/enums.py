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
    OFFLOAD = "OFFLOAD"


class Trigger(StrEnum):
    SCHEDULE = "SCHEDULE"
    WATCHER = "WATCHER"
    MANUAL = "MANUAL"
    INITIAL = "INITIAL"
    RESOLVE = "RESOLVE"
    RECOVERY = "RECOVERY"
    RESYNC = "RESYNC"
    APP = "APP"  # a file action in the web UI (new folder, rename, move, delete)
    OFFLOAD = "OFFLOAD"


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


class OffloadState(StrEnum):
    PENDING = "PENDING"  # recorded and out of sync; Drive not touched yet
    DELETING = "DELETING"  # rclone is removing it from Drive (into the trash)
    VERIFYING = "VERIFYING"  # checking every Drive ID is in the trash
    ACTIVE = "ACTIVE"  # done: only on this server, Drive copy in the trash
    ATTENTION = "ATTENTION"  # something to look at; still out of sync
    RESTORING = "RESTORING"  # back in sync; waiting for the safe resync that uploads it
    RESTORED = "RESTORED"  # back on Drive


class Provider(StrEnum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"  # later: OneDrive and Outlook


class ServiceKind(StrEnum):
    FILES = "files"
    MAIL = "mail"


class ServiceState(StrEnum):
    NOT_SET_UP = "not_set_up"  # shows a "Set up" card
    SETTING_UP = "setting_up"  # permission given; waiting for the owner to start
    ACTIVE = "active"
    NEEDS_RECONNECT = "needs_reconnect"  # the login or the permission went away
    PAUSED = "paused"  # in the model for a later "pause mail" switch; nothing sets it yet
