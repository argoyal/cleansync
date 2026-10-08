# Architecture

One container, one process.

- **FastAPI** serves the REST API (`/api/*`, docs at `/api/docs` after login) and a Server-Sent Events stream (`/api/events`) for live status.
- **SQLite** (`app/data/drivesync.db`) holds accounts, sync pairs, job history, conflicts and settings. There is no row per file.
- **rclone** is the only thing that touches Google Drive file content. DriveSync runs it as a subprocess with a fixed list of arguments (`backend/app/services/rclone/commands.py`), never through a shell.
- **Queue**: at most one job per sync pair; extra requests while a pair is running collapse into one follow-up run. A small number of pairs (default 2) run at once.
- **Scheduler** (APScheduler) asks the queue for a sync every N minutes per pair, refreshes Drive quota hourly, and runs daily maintenance (database copy, pruning old backups and logs).
- **Watcher** (watchdog) asks the queue for a sync a few seconds after local files stop changing. If the folder does not deliver change events (common on Windows-mounted or network drives), the watcher reports "limited" and the schedule covers it.

## One sync
1. Regenerate the pair's filter file. If it changed, run a safe resync instead (never deletes).
2. `rclone bisync --dry-run` and count planned deletions on both sides.
3. More than the limit: the job is BLOCKED, nothing changes, the list is saved for review.
4. Real `rclone bisync`. Progress is streamed live.
5. Record counts, conflicts and errors.

## Folder layout
    /drivesync/
      app/data/   drivesync.db, db-backups/, rclone/ (encrypted rclone.conf, bisync state),
                  filters/, previews/, backups/, logs/, secrets/
      storage/<email>/   your files, one folder per Google account

Path1 is always the local folder; Path2 is always Google Drive.
