export type Me = { username: string; must_change_password: boolean; csrf_token: string };

export type AccountStatus = "CONNECTED" | "NEEDS_REAUTH" | "DISCONNECTED";
export type ServiceState = "not_set_up" | "setting_up" | "active" | "needs_reconnect" | "paused";
export type Account = {
  id: number;
  email: string;
  status: AccountStatus;
  provider: string;
  quota_total: number | null;
  quota_used: number | null;
  quota_trash: number | null;
  connected_at: string;
  last_checked_at: string | null;
  services: { files: ServiceState; mail: ServiceState };
  /** Google gave CleanSync permission to read and manage mail at the last sign-in. */
  mail_permission: boolean;
  /** Allow permanent delete is on: the owner's switch, and Google allows it (bulk offload spec §3). */
  permanent_delete: boolean;
  /** Why it went off by itself, when Google took the permission back. */
  permanent_delete_note: string | null;
  /** CleanSync holds this mailbox's Google profile photo (served at /api/accounts/{id}/photo). */
  has_photo: boolean;
};

export type PairStatus = "NEW" | "HEALTHY" | "RUNNING" | "ATTENTION" | "ERROR" | "PAUSED";
export type Direction = "BIDIRECTIONAL" | "REMOTE_TO_LOCAL" | "LOCAL_TO_REMOTE";
export type Pair = {
  id: number;
  account_id: number;
  account_email: string;
  name: string;
  local_subpath: string;
  local_path: string;
  remote_path: string;
  direction: Direction;
  schedule_interval_s: number | null;
  paused: boolean;
  max_deletions: number;
  backups_enabled: boolean;
  backup_retention_days: number | null;
  initialized: boolean;
  status: PairStatus;
  attention_reason: string | null;
  last_error: string | null;
  last_sync_at: string | null;
  last_success_at: string | null;
  last_error_at: string | null;
  next_sync_at: string | null;
};

export type Status = {
  accounts: number;
  accounts_attention: number;
  pairs: number;
  new: number;
  healthy: number;
  attention: number;
  running: number;
  paused: number;
  conflicts: number;
  sync_stopped: string | null;
};

/** Per-item status in the file browser. */
export type ItemStatus =
  | "synced"
  | "syncing"
  | "upload"
  | "download"
  | "conflict"
  | "native"
  | "offloaded"
  | "unsynced"
  | "unknown";

export type FileItem = {
  name: string;
  path: string;
  is_dir: boolean;
  size: number | null;
  mtime: string | null;
  status: ItemStatus;
  local: boolean;
  remote: boolean;
  mime: string | null;
  native: string | null;
  google_url: string | null;
  starred: boolean;
  pair_id: number | null;
  thumb: boolean;
  pair_root?: boolean; // a sync pair's own folder (shown with a sync mark)
};

export type PairBrief = {
  id: number;
  name: string;
  status: PairStatus;
  initialized: boolean;
  local_subpath: string;
  direction?: Direction;
};
export type Browse = { account_id: number; path: string; pair: PairBrief | null; items: FileItem[] };

/** The answer to a rename, move, delete or new folder. */
export type OpOut = {
  paths: string[];
  syncs: Record<string, string>;
  /** New paths of the items that were renamed or moved on Google Drive too. */
  drive?: string[];
};

export type Storage = {
  account_id: number;
  email: string;
  drive: { total: number | null; used: number | null; trash: number | null; checked_at: string | null };
  disk: { total: number; used: number; free: number };
  synced_bytes: number | null;
};

export type Setup = {
  oauth_configured: boolean;
  redirect_uri: string;
  public_url: string;
  insecure_http: boolean;
  accounts: number;
  pairs: number;
  initialized_pairs: number;
};

export type SystemInfo = {
  app_name: string;
  app_version: string;
  python_version: string;
  rclone_version: string;
  storage_root: string;
  data_dir: string;
  watcher: string;
  db_ok: boolean;
  insecure_http: boolean;
  public_url: string;
};

export type AppSettings = {
  default_interval_s: number | null;
  default_max_deletions: number;
  default_backups_enabled: boolean;
  default_backup_retention_days: number | null;
  watcher_debounce_s: number;
  max_parallel_pairs: number;
  session_idle_hours: number;
  log_retention_days: number;
  mail_check_interval_s: number;
  mail_min_age_days: number;
  mail_disk_floor_gb: number;
  google_daily_budget_units: number;
};

export type Job = {
  id: number;
  pair_id: number;
  kind: string;
  trigger: string;
  state: string;
  started_at: string | null;
  completed_at: string | null;
  duration_s: number | null;
  exit_code: number | null;
  files_uploaded: number;
  files_downloaded: number;
  files_deleted_local: number;
  files_deleted_remote: number;
  files_changed: number;
  conflicts: number;
  planned_deletions: number;
  bytes_transferred: number;
  override_used: boolean;
  error_message: string | null;
};

export type Conflict = {
  id: number;
  pair_id: number;
  path: string;
  local_variant: string;
  remote_variant: string;
  local_size: number | null;
  local_mtime: string | null;
  remote_size: number | null;
  remote_mtime: string | null;
  state: string;
  resolution: string | null;
};

export type ActivityEvent = {
  id: number;
  ts: string;
  level: string;
  kind: string;
  message: string;
  account_id: number | null;
  pair_id: number | null;
};

export type Planned = {
  job_id: number | null;
  changes: { side: "local" | "remote"; action: "delete" | "copy"; path: string }[];
};

export type Preview = {
  job_id: number;
  created_at: string;
  same: number;
  only_local: number;
  only_remote: number;
  different: number;
  errors: number;
  samples: { only_local: string[]; only_remote: string[]; different: string[]; errors: string[] };
};

export type CatalogSummary = {
  pair_id: number;
  refreshed_at: string | null;
  refreshing: boolean;
  error: string | null;
  files: number;
  folders: number;
  bytes: number;
  native_docs: number;
};

export type Transfer = { name: string; size?: number; bytes?: number; percentage?: number; speed?: number };
export type Progress = {
  pair_id: number;
  job_id: number;
  bytes: number;
  total_bytes: number;
  speed: number;
  eta: number | null;
  transferring: Transfer[];
};

export type FolderEntry = { name: string; path: string };

export type OffloadState = "PENDING" | "DELETING" | "VERIFYING" | "ACTIVE" | "ATTENTION" | "RESTORING" | "RESTORED";
export type Offload = {
  id: number;
  pair_id: number;
  pair_name: string;
  account_id: number;
  /** Account path of the offloaded file or folder. */
  path: string;
  is_dir: boolean;
  file_count: number;
  total_bytes: number;
  state: OffloadState;
  trash_verified: boolean;
  trash_emptied: boolean;
  created_at: string;
  updated_at: string;
  note: string | null;
  /** Being deleted from Google Drive's trash right now. */
  busy: boolean;
};
export type OffloadPreviewItem = {
  path: string;
  pair_id: number | null;
  pair_name: string | null;
  is_dir: boolean;
  file_count: number;
  total_bytes: number;
  /** Google Docs, Sheets and the like inside it: skipped, they stay on Drive. */
  stays_on_drive: string[];
  /** Why this item can't be offloaded, or null. */
  blocked: string | null;
};
export type OffloadPreview = {
  items: OffloadPreviewItem[];
  /** Totals of the items that can go. */
  total_files: number;
  total_bytes: number;
  /** Why nothing can start right now (syncing stopped), or null. */
  stopped: string | null;
};
export type OffloadBatchItem = {
  path: string;
  state: "waiting" | "running" | "done" | "attention" | "failed";
  message: string | null;
  offload_id: number | null;
};
export type OffloadBatch = { id: string; account_id: number; done: boolean; items: OffloadBatchItem[] };

/** Where a Recovery entry came from (spec §8). "resync": set aside in a safe resync. */
export type RecoveryKind = "sync_backup" | "deleted" | "first_sync" | "resync" | "conflict" | "offload";
export type RecoveryEntry = {
  /** Its path under the account's backups folder, or "offload:<id>". */
  id: string;
  kind: RecoveryKind;
  pair_id: number | null;
  pair_name: string | null;
  name: string;
  /** Account path it came from; null when its sync folder was removed. */
  original_path: string | null;
  size: number | null;
  day: string; // YYYY-MM-DD
  /** First day it may be cleaned up (YYYY-MM-DD); null = kept until deleted. */
  expires_on: string | null;
  restorable: boolean;
  offload_id: number | null;
  /** An offload past Google's 30-day trash: Google has emptied it from its trash. */
  google_emptied: boolean;
};
export type RecoveryList = { total: number; entries: RecoveryEntry[] };
export type RestoreResult = { id: string; restored_to: string | null; error: string | null };
export type GoogleUsage = { day: string; used: number; budget: number; drive_estimate: number; reached: boolean };

export type MailPhase = "initial" | "incremental" | "recheck";
export type MailFailure = { provider_id: string; attempts: number; last_error: string };
export type MailJobBrief = { id: number; kind: string; state: string; started_at: string; completed_at: string | null; done: number; message: string | null };
export type MailStatus = {
  account_id: number;
  email: string;
  service: ServiceState;
  phase: MailPhase | null;
  stored: number;
  stored_bytes: number;
  total: number | null;
  running: boolean;
  waiting: boolean;
  time_left_s: number | null;
  last_run_at: string | null;
  last_error: string | null;
  job: MailJobBrief | null;
  failed: MailFailure[];
  budget_reached: boolean;
};
export type MailSetupInfo = { total: number; size_bytes: number | null };
export type ActivityJob = {
  source: "files" | "mail";
  id: number;
  account_id: number;
  title: string;
  kind: string;
  state: string;
  started_at: string | null;
  completed_at: string | null;
  message: string | null;
  log_url: string;
};

export type MailCounts = { views: Record<string, number>; labels: { id: string; name: string; count: number }[] };
export type DecisionChip = {
  id: number;
  rule_name: string;
  status: "suggested" | "applied" | "planned";
  how: string;
  why: string;
  /** False when the mail left Gmail for good: Undo can't bring it back. */
  undoable: boolean;
  /** The rule's action is Delete forever. */
  forever: boolean;
};
export type ThreadRow = {
  thread_id: string;
  count: number;
  last_date: string;
  message_id: number;
  from_addr: string;
  subject: string;
  snippet: string;
  has_attachments: boolean;
  unread: boolean;
  offloaded: boolean;
  deleted: boolean;
  decision: DecisionChip | null;
};
export type ThreadPage = { threads: ThreadRow[]; next_cursor: string | null; total: number | null; total_bytes: number | null };
/** How many mails a view or search has, and how big they are (its own request, issue #16). */
export type MailTotals = { total: number; total_bytes: number };
export type MailAttachment = { index: number; name: string; type: string; size: number; preview: boolean };
export type MailMessageInfo = {
  id: number;
  account_id: number;
  provider_id: string;
  thread_id: string;
  from_addr: string;
  to_addrs: string;
  cc_addrs: string;
  subject: string;
  date: string;
  size: number;
  file_size: number;
  labels: { id: string; name: string }[];
  state: "in_mail" | "offloaded" | "deleted_in_provider";
  offload_id: number | null;
  attachments: MailAttachment[];
  /** Known for one opened mail (`useMailMessage`); null in a thread list. */
  has_remote_images: boolean | null;
  gmail_url: string | null;
  file_missing: boolean;
  decision: DecisionChip | null;
};

/** Mail offload and restore (spec §5). Named Mail… to stay apart from the Drive offload types. */
export type MailBrief = { id: number; subject: string; from_addr: string; date: string; size: number };
export type MailOffloadPreview = {
  filter_text: string;
  count: number;
  bytes: number;
  largest: MailBrief[];
  oldest: MailBrief[];
  /** Mail left out, each with how many and why, already written for people. */
  excluded: { reason: string; count: number; text: string }[];
};
/** What an offload or restore starts from: a search, picked mails, or picked threads. */
/** Pick all (mail panel spec §6): every mail of the view and search, worked out by the server. */
export type MailPickAll = { view: string; label?: string; q: string };
export type OffloadStart = { q: string } | { message_ids: number[] } | { thread_ids: string[] } | MailPickAll;
export type MailOffloadState = "WAITING" | "RUNNING" | "DONE" | "DONE_WITH_SKIPS" | "FAILED";
export type MailOffload = {
  id: number;
  account_id: number;
  filter_text: string;
  state: MailOffloadState;
  total: number;
  done: number;
  skipped: number;
  not_done: number;
  bytes: number;
  reason: string | null;
  created_at: string;
  finished_at: string | null;
  /** The day Gmail empties these from its trash by itself (30 days after the offload). */
  trash_until: string; // YYYY-MM-DD
  google_emptied: boolean;
  /** "delete_forever": mail deleted from Gmail and CleanSync (bulk offload spec §5). */
  kind: "offload" | "delete_forever";
  /** Removed from Gmail for good: the space is free now. */
  removed: number;
  /** In Gmail's trash until Gmail empties it. */
  trashed: number;
  /** The owner pressed Stop (the server says so; the reason's wording is not to be matched). */
  stopped: boolean;
};
export type MailOffloadDetail = MailOffload & {
  skipped_items: { message_id: number; subject: string; from_addr: string; date: string; reason: string }[];
};
export type MailRecoveryData = { offloads: MailOffload[]; deleted_count: number };
