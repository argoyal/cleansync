# Known limitations

- Google Docs, Sheets and Slides created in Google are not synced as files (they have no file content). They will be shown and linked in the UI; a read-only export and manual conversion come later.
- Shared Drives and "Shared with me" are not synced yet.
- Every sync lists both sides twice (dry run + real run). Very large Drives may need a longer interval.
- Resolving a conflict removes its two conflict copies on Drive in the next sync, which counts as two deletions. So with a limit of 10, resolving 6 conflicts at once (12 deletions) blocks the next sync; use "Override once" or raise the limit.
- rclone keeps its sync memory in files named after both folder paths. Very deep pair folders can hit the 255-character file-name limit.
- Local edits made while a sync is running are picked up by the next scheduled sync, because the file watcher ignores changes during a run.
- On Windows-backed drives, names that differ only by letter case are treated as the same file, and change notifications may not arrive (the watcher shows "limited").
- Characters that Windows does not allow in file names are still to be checked in end-to-end testing.
- A two-way sync has no hard cap on the *number* of deletions between its dry run and its real run: bisync only offers a percentage brake (50%). Files deleted in the seconds between the check and the run are therefore not limited by the count you approved; they still go to Drive's trash, or to the local backup folder when backups are on.
- The first time you browse a folder right after a sync, an item can still show its status from before the sync for a moment. It corrects itself within about a minute.
- Folders are listed in one piece, not page by page. A folder with a huge number of items (tens of thousands) can be slow to open.
- On disks that ignore letter case (Windows, some network shares), two names that differ only by case are shown as two items, even though the disk treats them as the same file.
- Renaming or moving an item in CleanSync is done on Drive too, as a real rename, so the Drive file keeps its ID and sharing.
- Changing only the letter case of a folder name (for example `photos` to `Photos`) can fail on case-insensitive storage. If it does, nothing changes and you see an error.
- A development build that was made without the Office template generator cannot create blank Word, Excel or PowerPoint files (the container image always includes them).
- Google Docs, Sheets and Slides are recognised in the Drive listing by their Google file type or their missing size; this is still to be confirmed against a real Drive.
- Offloading changes the sync rules of its folder, so the next sync is a safe resync. A file deleted on one side whose deletion had not synced yet comes back (see "After an upgrade that changes the sync rules" below). Sync before offloading to avoid it.
- Offloading a folder also sends its empty sub-folders to Google Drive's trash; "Empty from trash now" deletes the offloaded files, not those empty folders, so Google removes them when it empties its trash.
- An offload that needs a look ("Needs a look") stays out of sync until you restore it to Drive. There is no "check again" button yet.
- A sync folder that has offloaded items can't be removed until they are restored to Drive (otherwise syncing that folder again later would upload them again).
- You can't add files inside an offloaded folder (new items, uploads or moves into it) until it is restored to Drive.
- An offloaded item can't be renamed or moved until it is restored. Deleting it (or a folder or selection holding it) moves it to Recovery as usual, after a warning; after the backup retention it is gone for good, because Drive no longer has it.
- Offload and Restore to Drive wait until a folder whose sync stopped for you to look at it (too many deletes, the storage check, a needed resync) is sorted out.
- A pretend local account (used for testing) has no Drive IDs and no trash, so offload skips the trash check there and says so.
- The automated tests use a local folder in place of Google Drive, which has no trash; Drive's trash is checked with the Drive API and in the manual end-to-end test with a real Google account.
- A folder named `conflicts` at the top of a sync folder shares its backup folder name with the versions not kept in conflicts; the Recovery page then lists its backups as conflict versions.
- A file restored from Recovery keeps the date it had when it was saved. If Google Drive has a file with the same name that hasn't synced here yet, nothing is lost, but the result depends on the case. If the name is new on both sides, the next sync keeps both copies as a conflict (`name.conflict1.ext` is the restored one, `name.conflict2.ext` is Drive's) and you choose in Conflicts. If it is a file that was already synced and Drive changed it, Drive's version keeps the name and the restored copy goes back into Recovery (when backups are on for that folder).
- The Recovery page reads the whole backups folder each time it opens, so a very large backups folder makes it slow.

## After an upgrade that changes the sync rules

When a new version changes the list of files that never sync (for example, the version that started skipping `.drivesync-upload-*` upload copies), every two-way folder runs one automatic safe resync on its next sync. A safe resync merges both sides and never deletes. The catch: a file you deleted on one side, whose deletion had not synced yet, comes back on that side instead of being deleted on the other. Delete it again after the resync.

## Mail

- CleanSync backs up, offloads and sorts mail; it does not send mail, invitations or replies. The reader doesn't mark mail read or add labels by hand; that is done only by [mail rules](mail-rules.md). Delete moves mail to Gmail's trash and keeps CleanSync's copy in the Local archive.
- Gmail only for now; Outlook comes later.
- Spam, Trash, drafts and chats are not backed up.
- Mail that cannot be downloaded from Gmail is never offloaded, and the offload preview does not count it among the mails it leaves out.
- Offloaded mail goes to Gmail's trash and Gmail empties it after 30 days. CleanSync cannot empty it sooner: that needs Google's full mailbox permission, and CleanSync asks only for `gmail.modify`.
- Offloads wait while the mail backup is checking the mailbox.
- A mail with no usable Message-ID header (missing, or with characters a Gmail search can't take) that is restored again after Gmail's answer got lost could end up twice in Gmail (rare). Other mail is never imported twice.
- A restore that is queued but has not started yet is lost if CleanSync stops or the storage disk is missing at that moment. The mails stay safe (still offloaded); ask for the restore again.
- CleanSync waits at most 60 seconds on each step of sending a restored mail to Gmail. A very large mail on a slow upload connection may not make it; the restore then stops with "CleanSync couldn't reach Gmail" and the rest stays offloaded. Try again from a faster connection.
- Restoring an old database copy: mail offloaded after that copy needs taking out of Gmail's trash within 30 days (see [backup and recovery](backup-recovery.md#mail)).
- The size shown before the first backup is what Gmail and Google Photos use together; Google doesn't report Gmail alone.
- Images inside a mail are shown up to 10 MB per mail; the mail frame has a fixed height and scrolls by itself.
- If CleanSync stops right after moving a mail to Gmail's trash but before marking it offloaded, that mail shows as "Deleted in Gmail" after the next check; restore it from there.

## Mail rules

- One rule per mail.
- A small model gets some mail wrong, so start new rules in Suggest mode and switch them to Automatic once their suggestions look right.
- A run of rules on older mail covers at most 20,000 mails at a time.
- The record a Delete forever leaves in the Activity log keeps the mail's sender, subject and date.
- Only new inbox mail is sorted automatically; mail a Gmail filter sends straight to a label is not.
- The model's server sees the text CleanSync sends it (up to 2,000 characters per mail). Use a local model if that matters.
