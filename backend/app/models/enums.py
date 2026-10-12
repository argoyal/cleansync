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
    PAUSED = "PAUSED"  # waits for something outside CleanSync (budget, disk space)


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


class MailState(StrEnum):
    IN_MAIL = "in_mail"  # in Gmail, and kept here
    OFFLOADED = "offloaded"  # CleanSync moved it out of Gmail; kept here
    DELETED = "deleted_in_provider"  # deleted in Gmail by someone else; kept here
    # Deleted by a rule or by hand: in Gmail's trash (or gone from it), kept here as archive
    # only, out of the main views and search (AI rules spec §7).
    LOCAL_ARCHIVE = "local_archive"


class MailPhase(StrEnum):
    INITIAL = "initial"  # the first backup: listing every mail
    INCREMENTAL = "incremental"  # keeping up from Gmail's history
    RECHECK = "recheck"  # history too old: listing every mail again


class MailJobKind(StrEnum):
    BACKUP = "BACKUP"  # part of the first backup
    CHECK = "CHECK"  # keeping up
    RECHECK = "RECHECK"
    OFFLOAD = "OFFLOAD"
    RESTORE = "RESTORE"


class MailOffloadState(StrEnum):
    WAITING = "WAITING"  # before it starts, or paused by the budget; goes on by itself
    RUNNING = "RUNNING"
    DONE = "DONE"
    DONE_WITH_SKIPS = "DONE_WITH_SKIPS"
    FAILED = "FAILED"  # with a reason; what was not reached stayed in Gmail


class MailItemResult(StrEnum):
    PENDING = "pending"
    OFFLOADED = "offloaded"
    SKIPPED = "skipped"  # stayed in Gmail; the reason says why
    RESTORED = "restored"  # put back in Gmail later


class RuleMode(StrEnum):
    SUGGEST = "suggest"  # decisions wait for the owner
    AUTOMATIC = "automatic"  # decisions are applied as soon as they are made


class RuleCombine(StrEnum):
    """How a rule's fixed conditions combine with its description (AI rules spec §5)."""

    FIXED_ONLY = "fixed_only"  # no AI call
    FIXED_DECIDES = "fixed_decides"  # a fixed match applies it; else the AI is asked
    FIXED_THEN_AI = "fixed_then_ai"  # a fixed match makes it a candidate; the AI confirms
    AI_ONLY = "ai_only"


class DecisionHow(StrEnum):
    REMEMBERED = "remembered"
    FIXED = "fixed"
    AI = "ai"
    NONE = "none"  # no rule fitted
    MANUAL = "manual"  # the owner's own Delete or correction, logged so it can be undone


class DecisionStatus(StrEnum):
    PLANNED = "planned"  # a rule job's preview; applied when the owner confirms the job
    SUGGESTED = "suggested"
    APPLIED = "applied"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"  # the owner moved the mail, or the rule went, before approval
    UNDONE = "undone"
    FAILED = "failed"
    NO_RULE = "no_rule"
    SKIPPED = "skipped"  # a rule job's mail that changed since the preview


class RuleJobState(StrEnum):
    PREVIEWING = "PREVIEWING"
    PREVIEWED = "PREVIEWED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class AiTaskName(StrEnum):
    SORTING = "sorting"
    WRITING_RULES = "writing_rules"  # falls back to SORTING's choice when not set
