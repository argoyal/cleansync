# Backup and recovery

## What to back up
Back up `app/data/`. It holds the database (including which paths are offloaded), the encrypted Google logins, rclone's sync memory, and the safety backups. Keep `RCLONE_CONFIG_PASS` (or `app/data/secrets/`) with it: without the password the saved logins cannot be read.

`storage/` is your synced files; back it up like any other data.

## Built-in safety copies
- Every night DriveSync copies the database to `app/data/db-backups/drivesync-YYYY-MM-DD.db` (last 14 kept).
- Local files replaced or deleted by a sync go to `app/data/backups/<email>/<pair>/<date>/` and are kept for the pair's retention (7/30/90 days or forever).
- Before the first sync, local versions of files that differ from Drive go to `…/<date>/initial/`.
- Files deleted on Drive go to Drive's trash (kept 30 days by Google).

## Restore the database
1. Stop the container.
2. Copy the newest good `app/data/db-backups/drivesync-*.db` over `app/data/drivesync.db`.
3. Start the container. Pairs come back; the next sync uses rclone's `--recover`, and DriveSync never assumes the last run finished.

If DriveSync starts and reports "database integrity check failed", it will not sync until you restore.
