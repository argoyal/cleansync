# Architecture

One container, one process.

- **FastAPI** serves the REST API (`/api/*`, docs at `/api/docs` after login) and a Server-Sent Events stream (`/api/events`) for live status.
- **SQLite** (`app/data/drivesync.db`) holds accounts, sync pairs, job history, conflicts and settings. There is no row per file.
- **rclone** is the only thing that touches Google Drive file content. CleanSync runs it as a subprocess with a fixed list of arguments (`backend/app/services/rclone/commands.py`), never through a shell.
- **Queue**: at most one job per sync pair; extra requests while a pair is running collapse into one follow-up run. A small number of pairs (default 2) run at once.
- **Scheduler** (APScheduler) asks the queue for a sync every N minutes per pair, refreshes Drive quota hourly, and runs daily maintenance (database copy, pruning old backups and logs).
- **Watcher** (watchdog) asks the queue for a sync a few seconds after local files stop changing. If the folder does not deliver change events (common on Windows-mounted or network drives), the watcher reports "limited" and the schedule covers it.
- **Web app** (React, built into the image) is served by FastAPI; any path outside `/api`, `/files` and `/assets` returns the app, which handles its own routes.
- **Catalog**: per pair, a cached Google Drive listing (`rclone lsjson -R --fast-list --metadata`) in `app/data/catalog/<pair>.json`, with Drive IDs, MIME types and stars. It is refreshed after a sync that changed files, after connecting an account or creating a pair, on request, and when older than an hour. The file browser compares it with the disk to mark each item synced, waiting to upload or waiting to download.
- **drive_api** makes the one Google Drive API call rclone cannot: creating a blank Google Doc, Sheet or Slides.

## One sync
1. Regenerate the pair's filter file. If it changed, run a safe resync instead (never deletes).
2. `rclone bisync --dry-run` and count planned deletions on both sides.
3. More than the limit: the job is BLOCKED, nothing changes, the list is saved for review.
4. Real `rclone bisync`. Progress is streamed live.
5. Record counts, conflicts and errors.

Accounts have a provider (Google today) and one row per service (Files, Mail) in account_services. Each service reaches its provider through an adapter (app/services/adapters/): Files covers the Drive calls made outside rclone's sync runs.

## Mail
Gmail is reached through a Gmail adapter (`app/services/mail/gmail.py`) on top of one shared Google client that paces every call and counts it against a daily budget (default 10 million units, at most 40 million; Drive calls are counted but never held back). Mail work goes through a mail queue, one job per account at a time: backup and re-check, offload, and restore are queue actions, so they never overlap. Each mail is kept once as its original `.eml` under `mail/<email>/<year>/<month>/`, and the database holds its labels, state and an SQLite FTS5 search index. An offload checks the stored file, moves the mail to Gmail's trash (the narrow `gmail.modify` scope, so Gmail empties the trash itself after 30 days) and checks it is there; restore takes it out of the trash, or imports the stored `.eml` when Gmail no longer has it.

## Rules
Mail rules (`app/services/rules/`) sort new inbox mail with a model the owner picks in Settings → Models. One small client (`app/services/ai/client.py`, plain `httpx`, no SDK) talks to any OpenAI-style server; connections and the model for each task are in the database, and API keys and extra headers are files under `app/data/secrets/ai/`. When the backup keeps a new inbox mail, it adds a row to a to-do list (`rule_todo`) in the same transaction. A sorting worker, one asyncio task outside the mail queue (so a slow model never holds up backup, offload or restore), takes rows from that list, reads the mail from its stored `.eml` only, decides (remembered senders, then fixed conditions, then one question to the model), and writes a row to the decision log (`rule_decisions`) with the rule version, the reason and the exact label changes. Applying a decision is one `messages.modify` call (or `messages.trash` for Offload) through the shared, paced Google client; the decision log is what Undo and corrections reverse. "Run rules on…" is a rule job: the same worker decides every mail first as a preview, and makes the changes only after the owner types the count. Offloading mail (by hand, from a search, or by a rule) moves it to the one `OFFLOADED` state: out of Gmail (in its trash), the copy kept here, shown in the Offloaded view until it is restored or deleted forever. Delete forever removes CleanSync's own copy.

## Folder layout
    /drivesync/
      app/data/   drivesync.db, db-backups/, rclone/ (encrypted rclone.conf, bisync state),
                  filters/, previews/, catalog/, thumbs/, uploads/, backups/, logs/,
                  secrets/ (with ai/ for the model keys)
      storage/<email>/   your files, one folder per Google account
      mail/<email>/      your mail, one .eml per message

Path1 is always the local folder; Path2 is always Google Drive.
