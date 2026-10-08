# Known limitations

- Google Docs, Sheets and Slides created in Google are not synced as files (they have no file content). They will be shown and linked in the UI; a read-only export and manual conversion come later.
- Shared Drives and "Shared with me" are not synced yet.
- Every sync lists both sides twice (dry run + real run). Very large Drives may need a longer interval.
- Resolving a conflict removes its two conflict copies on Drive in the next sync. Resolving more than half your deletion limit's worth of conflicts at once will block that sync; use "Override once" or raise the limit.
- rclone keeps its sync memory in files named after both folder paths. Very deep pair folders can hit the 255-character file-name limit.
- Local edits made while a sync is running are picked up by the next scheduled sync, because the file watcher ignores changes during a run.
- On Windows-backed drives, names that differ only by letter case are treated as the same file, and change notifications may not arrive (the watcher shows "limited").
- Characters that Windows does not allow in file names are still to be checked in end-to-end testing.
