# Backup and recovery

## What to back up
Back up `app/data/`. It holds the database (including which paths are offloaded), the encrypted Google logins, rclone's sync memory, the safety backups, and the lists of offloaded files (`app/data/offloads/`). Keep `RCLONE_CONFIG_PASS` (or `app/data/secrets/`) with it: without the password the saved logins cannot be read.

`storage/` is your synced files; back it up like any other data.

Also keep `.drivesync-volume` (in the data folder, next to `app/` and `storage/`). It tells CleanSync it is looking at the right disk.

## Built-in safety copies
- Every night CleanSync copies the database to `app/data/db-backups/drivesync-YYYY-MM-DD.db` (last 14 kept).
- Local files replaced or deleted by a sync go to `app/data/backups/<email>/<pair>/<date>/` and are kept for the pair's retention (7/30/90 days or forever).
- Before the first sync, local versions of files that differ from Drive go to `…/<date>/.drivesync-initial-<job_id>/` (and before a safe resync, to `…/<date>/.drivesync-resync-<job_id>/`).
- When you resolve a conflict, the version you didn't keep goes to `…/<date>/conflicts/`.
- Items you delete in the web app are moved to `…/<pair>/<date>/.drivesync-deleted-<time>-<id>/` (or `app/data/backups/<email>/unsynced/…` for items outside every sync pair; those are pruned after the default backup retention in Settings, 30 days unless you change it, and kept for good when it is set to "Keep forever"). The next sync then sends the Drive copy to Drive's trash.
- Files deleted on Drive go to Drive's trash (kept 30 days by Google).

## Recovery page
Every copy CleanSync set aside in `app/data/backups/` shows on the Recovery page, one line per file. CleanSync's own hidden files (`RCLONE_TEST`, `.google-exports`, half-finished upload copies), links, and anything CleanSync didn't put there itself are left out. Each line shows:
- where it came from: replaced or deleted by a sync, deleted in CleanSync, saved before the first sync, saved before a safe resync, or the version not kept in a conflict;
- where it goes back (the path it had);
- its size and date;
- when it will be cleaned up. That follows the sync folder's backup retention, or the default retention in Settings for items that were outside every sync folder. "No clean-up date (automatic clean-up is off)" means no retention applies (it is set to keep forever, or its sync folder was removed); such files stay in `app/data/backups/` until you remove them there yourself.

Files CleanSync sent to Google Drive's trash in an offload are listed too, with the day Google will empty them from its trash. Only offloads that got as far as removing files from Drive are listed; one that stopped before that still has its files on Drive. They are not restored here: use Restore to Drive on the Offloaded page. Once that day has passed, the entry says "Google has emptied this from its trash" and offers nothing more to do on Drive.

Restore copies the file back (the copy in Recovery stays) and queues a sync. It never overwrites: if the name is taken, the copy is called `name (restored).ext`, then `name (restored 2).ext`, and so on. You can pick several items and restore them at once, or download them as one zip (up to 1000 files at a time). Before a download starts, CleanSync checks the files are still there; if some were cleaned up in the meantime, the page says so and refreshes the list. Restore is refused, for that item only, when:
- its sync folder was removed (download it instead);
- the folder it goes back to is gone, for example because the disk is not mounted (download it instead);
- it goes into a folder that only receives changes from Google Drive.

While syncing is stopped (the storage disk is missing), nothing can be restored; downloads still work. An item that was deleted outside every sync folder is put back on this server only, with no sync. The same goes for an item deleted from inside an offloaded folder: it goes back into that folder and stays out of sync with it until the folder is restored to Drive. Right-click any item in My Drive and choose "Show in Recovery" to see only the copies of that item (or of anything inside that folder).

## Offload and Google Drive's trash
Offload keeps files on this server and removes them from Google Drive into Drive's trash, to free space on Drive. It only happens when you ask for it, never on its own. Pick one item or several and right-click, Offload…, or choose many at once with "Offload files…" on the Offloaded page.

- Google Docs, Sheets, Slides and Forms are skipped: CleanSync has no file copy of them, so they stay on Drive. A folder that holds one stays on Drive with it.
- A sync folder's own top folder can't be offloaded as a whole; pick the files and folders inside it.
- Offload works only in folders that sync both ways.

Before anything leaves Drive, CleanSync lists the files on Drive (with their Drive IDs), checks that the copy here matches every one of them (size and checksum), and saves that list to `app/data/offloads/<id>.json`. It then removes only the files on that list, and after that the folders the offload left empty; those go to Drive's trash too. Afterwards it checks that every Drive ID is in the trash and is still the file it checked. If anything does not match, the offload is marked "Needs a look" and stays out of sync.

Google empties its trash after 30 days. "Empty from trash now" deletes for good straight away, but only the Drive IDs of that one offload, only files that are still in the trash, and only after checking the copy here again. It can't be undone on Google Drive; the copy on this server stays.

Restore to Drive puts an offload back in sync. The next sync is a safe resync, which never deletes, and it uploads the files again. If Google Drive meanwhile has a newer file with the same name, Drive's version is kept and your copy is saved in Recovery (as "Saved before a safe resync"), even when backups are off for that folder.

Deleting an offloaded item in CleanSync moves it to Recovery as usual. Its copy there is then the only one, and it is removed for good once the backup retention runs out. The same is true when you delete a folder that holds an offloaded item, or a selection that includes one: the delete dialog names the offloaded items and says how long Recovery keeps them, and CleanSync refuses the delete until you have seen that warning.

Offload and Restore to Drive are refused while a folder's sync has stopped and is waiting for you to look at it (too many deletes, the storage check, or a needed resync): deal with that first.

Back up `app/data/offloads/` with the rest of `app/data/`, and keep `storage/` backed up too: after an offload the copy on this server may be the only one.

How this is tested: the automated tests use a local folder in place of Google Drive, and a local folder has no trash. Drive's real trash is checked with the Drive API while offloading, and in the owner's manual end-to-end test with a real Google account.

## Restore the database
1. Stop the container.
2. Copy the newest good `app/data/db-backups/drivesync-*.db` over `app/data/drivesync.db`.
3. Start the container. Pairs come back; the next sync uses rclone's `--recover`, and CleanSync never assumes the last run finished.

If CleanSync starts and reports "database integrity check failed", it will not sync until you restore.

## "Storage disk is missing — syncing stopped"
On its first start CleanSync writes a random ID to `.drivesync-volume` in its folder and stores the same ID in the database. At startup and before every sync it checks the file is there and matches. If not, nothing syncs until CleanSync restarts, so an unplugged disk can never look like "all files were deleted" to Google Drive.

- **The disk was unplugged or not mounted:** reconnect it, check the folder shows your files, then restart CleanSync.
- **You moved CleanSync to a new disk:** copy `.drivesync-volume` along with `app/data/` and `storage/`.
- **The file was lost but the disk is the right one:** the app log names the ID it expects ("does not hold the expected ID …"). Stop CleanSync, write that ID on one line into `.drivesync-volume`, and start it again. CleanSync never recreates this file by itself once the database has an ID.
