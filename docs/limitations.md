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
- Renaming or moving an item in DriveSync is done on Drive too, as a real rename, so the Drive file keeps its ID and sharing.
- Changing only the letter case of a folder name (for example `photos` to `Photos`) can fail on case-insensitive storage. If it does, nothing changes and you see an error.
- A development build that was made without the Office template generator cannot create blank Word, Excel or PowerPoint files (the container image always includes them).
- Google Docs, Sheets and Slides are recognised in the Drive listing by their Google file type or their missing size; this is still to be confirmed against a real Drive.
