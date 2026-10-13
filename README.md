# CleanSync

CleanSync keeps Google Drive in sync with your own server and keeps a copy of every Gmail message, with a web page to watch it and safety checks so a sync can never quietly delete your files. It can offload old files and mail to free space on Google, with a way back.

CleanSync is a control panel around [rclone](https://rclone.org) `bisync`. rclone moves the files; CleanSync decides when, checks every plan before it runs, and asks you when something needs a human.

> Status: early development. Backend, web UI, offload and the Recovery page are in; Office conversion comes next.

## What it does
- Connect one or more Google accounts. Each syncs into its own folder: `storage/<email>/`.
- A web app that looks and works like Google Drive: browse, open files in a new tab (video can seek), download files and whole folders, rename, move and delete, each item marked synced, syncing, waiting or in conflict.
- Deleted items first go to a Recovery folder on the server, then to Drive's trash at the next sync.
- Start syncing any Drive folder from "Sync a Drive folder…" in the Files panel, or any local folder from its right-click menu; synced and not-synced folders are marked.
- Two-way sync on a schedule (every 1–60 minutes), when local files change, or when you click Sync now.
- **Before the first sync**, shows what is only local, only on Drive, and different on both, and waits for you to type a confirmation.
- **Before every sync**, does a dry run. If it would delete more files than your limit (default 10), it stops and shows you the list.
- Files replaced or deleted locally are copied to a backup folder first. Files deleted on Drive go to Drive's trash.
- If a file changed on both sides, both versions are kept and you choose.
- **One place for Files and Mail:** a rail on the left picks Files, Mail, Activity, Recovery or Settings; the panel beside it has the account switcher and that section's views. On a phone the rail is a bar along the bottom.
- **Offload:** keep files on the server and remove them from Google Drive (into Drive's trash) to free Drive space. Only when you ask, checked file by file before and after. Right-click, or pick many at once on the Offloaded page. Google Docs, Sheets and Slides stay on Drive. Restore to Drive any time.
- **Mail backup:** every Gmail message except Spam and Trash is kept on the server as its original `.eml`, kept up to date every few minutes. Set it up from the Mail section: [docs/mail-backup.md](docs/mail-backup.md).
- **Mail reader:** views, labels, conversations, search with operators, mail shown safely (no scripts, images only on request), attachments.
- **Mail offload and restore:** move old or large mail to Gmail's trash (Gmail empties it after 30 days) after checking the copy here, and put it back any time without duplicates.
- **Mail rules:** write rules in your own words ("archive newsletters I never read") and a model you choose (Ollama, a LiteLLM gateway, OpenRouter…) sorts new inbox mail by them. Rules suggest first, every change can be undone, and Offload takes mail out of Gmail and keeps CleanSync's copy in Offloaded, and Delete forever removes that copy: [docs/mail-rules.md](docs/mail-rules.md).
- **Recovery:** every copy CleanSync kept, with where it came from and when it is cleaned up. Restore one or many (never overwrites), or download them.
- In 0.2.0 the + New button (new folders, uploads, blank Office files, Google Docs/Sheets/Slides) is hidden while Files and Mail settle into one app. Its code stays, and it comes back in a later release.

## Quick start
1. Create a Google OAuth client: [docs/google-oauth-setup.md](docs/google-oauth-setup.md).
2. Copy `docker-compose.example.yml` to `compose.yml` and `.env.example` to `.env`; fill in `.env`. (Don't copy over `docker-compose.yml`: that one builds CleanSync from source for development.)
3. Make the data folder owned by user 1000: `sudo mkdir -p /srv/drivesync && sudo chown 1000:1000 /srv/drivesync`.
4. While the image is private, log in to the registry once: `docker login ghcr.io` (a GitHub token with `read:packages`).
5. `docker compose -f compose.yml pull && docker compose -f compose.yml up -d`. Run the same two commands again to update to the newest `latest` image.
   Upgrading from 0.1.0: the example compose service is now called `cleansync` (it was `drivesync`). Copy the new example over your `compose.yml` and run `docker compose -f compose.yml up -d --remove-orphans` once, so the old `drivesync` container is removed.
6. Log in and change the password. If you left `ADMIN_PASSWORD` empty, the first password is in `app/data/secrets/initial-admin-password`.

Open `PUBLIC_URL`, log in, choose your own password, and the setup wizard walks you through connecting Google, choosing the folders, checking the preview and starting the first sync. The REST API is described at `PUBLIC_URL/api/docs` (logged-in sessions only).

## Configuration
| Variable | Required | Meaning |
|---|---|---|
| `PUBLIC_URL` | yes | Address you open CleanSync at. https unless localhost/LAN. Google redirects back to `PUBLIC_URL/api/oauth/callback`. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | yes | Your own OAuth client. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | first start | Initial login. If no password is set, one is generated into `app/data/secrets/initial-admin-password`. |
| `RCLONE_CONFIG_PASS` | no | Encrypts the saved Google logins. Generated into `app/data/secrets/` on first start if empty. |
| `FORWARDED_ALLOW_IPS` | behind a proxy | Address of your reverse proxy (for example the Docker network gateway). Only these addresses may say which client address a request came from. Default `127.0.0.1`. See [docs/security.md](docs/security.md). |
| `TZ`, `LOG_LEVEL`, `APP_NAME` | no | Container time zone (log timestamps; syncing always uses UTC), log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`), name shown in the UI. |

## Docs
[Architecture](docs/architecture.md) · [Google OAuth setup](docs/google-oauth-setup.md) · [Mail backup](docs/mail-backup.md) · [Mail rules](docs/mail-rules.md) · [Security](docs/security.md) · [Backup and recovery](docs/backup-recovery.md) · [Troubleshooting](docs/troubleshooting.md) · [Known limitations](docs/limitations.md) · [Contributing](CONTRIBUTING.md)

## License
AGPL-3.0. If you run a modified CleanSync as a service for other people, you must publish your changes.
