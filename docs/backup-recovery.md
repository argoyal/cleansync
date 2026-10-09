# Backup and recovery

## What to back up
Back up `app/data/`. It holds the database (including which paths are offloaded), the encrypted Google logins, rclone's sync memory, and the safety backups. Keep `RCLONE_CONFIG_PASS` (or `app/data/secrets/`) with it: without the password the saved logins cannot be read.

`storage/` is your synced files; back it up like any other data.

Also keep `.drivesync-volume` (in the DriveSync folder, next to `app/` and `storage/`). It tells DriveSync it is looking at the right disk.

## Built-in safety copies
- Every night DriveSync copies the database to `app/data/db-backups/drivesync-YYYY-MM-DD.db` (last 14 kept).
- Local files replaced or deleted by a sync go to `app/data/backups/<email>/<pair>/<date>/` and are kept for the pair's retention (7/30/90 days or forever).
- Before the first sync, local versions of files that differ from Drive go to `…/<date>/.drivesync-initial-<job_id>/` (and before a safe resync, to `…/<date>/.drivesync-resync-<job_id>/`).
- When you resolve a conflict, the version you didn't keep goes to `…/<date>/conflicts/`.
- Items you delete in the web app are moved to `…/<pair>/<date>/.drivesync-deleted-<time>-<id>/` (or `app/data/backups/<email>/unsynced/…` for items outside every sync pair; those are pruned after the default backup retention in Settings, 30 days unless you change it, and kept for good when it is set to "Keep forever"). The next sync then sends the Drive copy to Drive's trash.
- Files deleted on Drive go to Drive's trash (kept 30 days by Google).

## Restore the database
1. Stop the container.
2. Copy the newest good `app/data/db-backups/drivesync-*.db` over `app/data/drivesync.db`.
3. Start the container. Pairs come back; the next sync uses rclone's `--recover`, and DriveSync never assumes the last run finished.

If DriveSync starts and reports "database integrity check failed", it will not sync until you restore.

## "Storage disk is missing — syncing stopped"
On its first start DriveSync writes a random ID to `.drivesync-volume` in its folder and stores the same ID in the database. At startup and before every sync it checks the file is there and matches. If not, nothing syncs until DriveSync restarts, so an unplugged disk can never look like "all files were deleted" to Google Drive.

- **The disk was unplugged or not mounted:** reconnect it, check the folder shows your files, then restart DriveSync.
- **You moved DriveSync to a new disk:** copy `.drivesync-volume` along with `app/data/` and `storage/`.
- **The file was lost but the disk is the right one:** the app log names the ID it expects ("does not hold the expected ID …"). Stop DriveSync, write that ID on one line into `.drivesync-volume`, and start it again. DriveSync never recreates this file by itself once the database has an ID.
