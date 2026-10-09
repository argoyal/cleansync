# Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| Pair shows **Blocked** | The dry run planned more deletions than the limit. | Review the list. If it is right, use "Override once" or raise the limit. |
| "Drive marker file RCLONE_TEST is missing" | One side lost its marker file, often because the disk is not mounted. Nothing was changed. | Check the mount. Then use "Run safe resync". |
| Account shows **Reconnect needed** | Google rejected the saved login (password change, access removed, or the consent screen is in Testing mode). | Reconnect from Settings → Accounts. Publish the consent screen to Production. |
| "rclone needs a safe resync" | rclone lost its record of the last sync. | Use "Run safe resync". It merges both sides and never deletes. |
| Watcher shows **limited** | The folder does not send change events (Windows/WSL or network drives). | Nothing; changes are picked up by the schedule. |
| Duplicate file names on Drive | Drive allows two files with the same name in one folder; sync cannot handle that. | Rename one in Drive, or run `rclone dedupe` by hand. |
| "Storage disk is missing — syncing stopped" | DriveSync could not find its disk ID file (`.drivesync-volume`) twice in a row, or found a different ID. Usually the disk was unplugged or not mounted. Nothing syncs, so Google Drive is not touched. | Reconnect the disk, check the mounted folder (for example `/mnt/d` on WSL) shows your files, then restart DriveSync. Moving to a new disk or restoring onto one: see [backup and recovery](backup-recovery.md). |
| Too many failed logins from the wrong address | Behind a proxy, `FORWARDED_ALLOW_IPS` is not set, so everyone looks like the proxy. | Set it to your proxy's address. See [security](security.md). |

Logs: each job's log can be downloaded from the pair's Logs tab, or found in `app/data/logs/jobs/<job id>.log`.
