export type Me = { username: string; must_change_password: boolean; csrf_token: string };

export type AccountStatus = "CONNECTED" | "NEEDS_REAUTH" | "DISCONNECTED";
export type Account = {
  id: number;
  email: string;
  status: AccountStatus;
  quota_total: number | null;
  quota_used: number | null;
  quota_trash: number | null;
  connected_at: string;
  last_checked_at: string | null;
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
