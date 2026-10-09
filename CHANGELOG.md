# Changelog

## Unreleased
- Backend core: login, Google account connection, sync pairs, first-sync preview and confirmation, scheduled and watched two-way sync with deletion limit, conflicts, history, logs and live events.
- Web UI modelled on Google Drive: file browser with sync status per item, open/download/zip, previews, uploads, new folder/rename/move/delete (deletes go to Recovery first), + New with Office and Google files, first-run wizard, sync activity, blocked-sync review, conflicts, settings and storage.
- Cached Drive listing per sync pair, security headers on every response.
- Syncing stops for every pair when the storage disk is missing (checked at startup, before every sync and before every file action; one failed read is retried a second later), with one clear message in the app. While stopped, the Sync switches say so, and turning a pair on or adding one is refused.
- Each sync pair has a Sync on / off switch. A new pair's local folder follows its Drive folder. Theme switch: System, Light or Dark.
- Add folders to sync from My Drive: New → "Sync a Drive folder…", and right-click a local folder → "Start syncing this folder". Synced folders and not-synced folders have their own marks.
