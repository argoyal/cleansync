# Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| Pair shows **Blocked** | The dry run planned more deletions than the limit. | Review the list. If it is right, use "Override once" or raise the limit. |
| "Drive marker file RCLONE_TEST is missing" | One side lost its marker file, often because the disk is not mounted. Nothing was changed. | Check the mount. Then use "Run safe resync". |
| Account shows **Reconnect needed** | Google rejected the saved login (password change, access removed, or the consent screen is in Testing mode). | Reconnect from Settings → Accounts. Publish the consent screen to Production. |
| "rclone needs a safe resync" | rclone lost its record of the last sync. | Use "Run safe resync". It merges both sides and never deletes. |
| Watcher shows **limited** | The folder does not send change events (Windows/WSL or network drives). | Nothing; changes are picked up by the schedule. |
| Duplicate file names on Drive | Drive allows two files with the same name in one folder; sync cannot handle that. | Rename one in Drive, or run `rclone dedupe` by hand. |
| "Storage disk is missing — syncing stopped" | CleanSync could not find its disk ID file (`.drivesync-volume`) twice in a row, or found a different ID. Usually the disk was unplugged or not mounted. Nothing syncs, so Google Drive is not touched. | Reconnect the disk, check the mounted folder (for example `/mnt/d` on WSL) shows your files, then restart CleanSync. Moving to a new disk or restoring onto one: see [backup and recovery](backup-recovery.md). |
| Too many failed logins from the wrong address | Behind a proxy, `FORWARDED_ALLOW_IPS` is not set, so everyone looks like the proxy. | Set it to your proxy's address. See [security](security.md). |
| "Paused: less than 2 GB free on the storage disk" | The disk is nearly full, so the mail backup stopped before writing. | Free some space (or lower the floor in Settings → Mail), then press Check now. |
| "Paused: CleanSync used its Google API budget for today" | Today's Google API budget (Settings → Google API) is used up. | Nothing to do: mail backup and offloads go on after midnight UTC. Raise the budget only if you're sure. |
| Pages hang while backups, sorting or unsubscribe run; the log says "database is locked" or "QueuePool limit … reached" | Background jobs are writing, and pages wait for the database. | If the data folder is on a local disk, set `DATABASE_WAL=1` and restart. Not on a network share or a Windows drive. |
| "Google asked CleanSync to slow down" | Gmail answered "too many requests" for a long time. | Nothing to do: it goes on at the next check. |
| "The Gmail API is turned off for this Google Cloud project" | The Gmail API is not enabled in your Google Cloud project. | In Google Cloud: APIs & Services → Library → Gmail API → Enable. Then try again. |
| Mail says "Reconnect mail" | Google no longer lets CleanSync read this account's mail (the permission was removed, or the login expired). | Press Reconnect mail and tick the Gmail box. Check `gmail.modify` is on the consent screen ([mail backup](mail-backup.md)). |
| "N mails couldn't be backed up after 3 tries" | Gmail would not hand over those mails. | CleanSync tries again once a day. If they keep failing, download them from Gmail by hand. They are never offloaded. |
| "The first mail backup hasn't finished yet" | Offload needs every mail backed up first. | Wait for the first backup to finish (Mail shows its progress). |
| "Wait until the mail backup has finished checking your mailbox" | An offload was asked for while the backup is checking the whole mailbox. | Wait until the check is done, then try again. |

Logs: each job's log can be downloaded from the pair's Logs tab, or found in `app/data/logs/jobs/<job id>.log`.
