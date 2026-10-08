# DriveSync

Keep a folder on your own server and Google Drive in two-way sync, with a web page to watch it, and safety checks so a sync can never quietly delete your files.

DriveSync is a control panel around [rclone](https://rclone.org) `bisync`. rclone moves the files; DriveSync decides when, checks every plan before it runs, and asks you when something needs a human.

> Status: early development (backend only; the web UI is coming next).

## What it does
- Connect one or more Google accounts. Each syncs into its own folder: `storage/<email>/`.
- Two-way sync on a schedule (every 1–60 minutes), when local files change, or when you click Sync now.
- **Before the first sync**, shows what is only local, only on Drive, and different on both, and waits for you to type a confirmation.
- **Before every sync**, does a dry run. If it would delete more files than your limit (default 10), it stops and shows you the list.
- Files replaced or deleted locally are copied to a backup folder first. Files deleted on Drive go to Drive's trash.
- If a file changed on both sides, both versions are kept and you choose.

## Quick start
1. Create a Google OAuth client: [docs/google-oauth-setup.md](docs/google-oauth-setup.md).
2. Copy `docker-compose.example.yml` to `compose.yml` and `.env.example` to `.env`; fill in `.env`. (Don't copy over `docker-compose.yml`: that one builds DriveSync from source for development.)
3. Make the data folder owned by user 1000: `sudo mkdir -p /srv/drivesync && sudo chown 1000:1000 /srv/drivesync`.
4. While the image is private, log in to the registry once: `docker login ghcr.io` (a GitHub token with `read:packages`).
5. `docker compose -f compose.yml pull && docker compose -f compose.yml up -d`. Run the same two commands again to update to the newest `latest` image.
6. Log in and change the password. If you left `ADMIN_PASSWORD` empty, the first password is in `app/data/secrets/initial-admin-password`.

The web UI arrives in a later release. Until then, use the REST API. Log in once to get the session cookie and the CSRF token, then send both with every request that changes something:

```sh
curl -c jar.txt -H 'content-type: application/json' \
  -d '{"username":"admin","password":"..."}' https://drivesync.example.com/api/auth/login
# -> {"csrf_token": "...", "must_change_password": true, ...}
curl -b jar.txt -c jar.txt -H 'content-type: application/json' -H 'X-CSRF-Token: <csrf_token>' \
  -d '{"current_password":"...","new_password":"at least 12 characters"}' \
  https://drivesync.example.com/api/auth/change-password   # returns a new csrf_token
curl -b jar.txt https://drivesync.example.com/api/openapi.json   # the full API description
```

The interactive API reference is at `PUBLIC_URL/api/docs`; it is only served to a logged-in session.

## Configuration
| Variable | Required | Meaning |
|---|---|---|
| `PUBLIC_URL` | yes | Address you open DriveSync at. https unless localhost/LAN. Google redirects back to `PUBLIC_URL/api/oauth/callback`. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | yes | Your own OAuth client. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | first start | Initial login. If no password is set, one is generated into `app/data/secrets/initial-admin-password`. |
| `RCLONE_CONFIG_PASS` | no | Encrypts the saved Google logins. Generated into `app/data/secrets/` on first start if empty. |
| `FORWARDED_ALLOW_IPS` | behind a proxy | Address of your reverse proxy (for example the Docker network gateway). Only these addresses may say which client address a request came from. Default `127.0.0.1`. See [docs/security.md](docs/security.md). |
| `TZ`, `LOG_LEVEL`, `APP_NAME` | no | Container time zone (log timestamps; syncing always uses UTC), log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`), name shown in the UI. |

## Docs
[Architecture](docs/architecture.md) · [Google OAuth setup](docs/google-oauth-setup.md) · [Security](docs/security.md) · [Backup and recovery](docs/backup-recovery.md) · [Troubleshooting](docs/troubleshooting.md) · [Known limitations](docs/limitations.md) · [Contributing](CONTRIBUTING.md)

## License
AGPL-3.0. If you run a modified DriveSync as a service for other people, you must publish your changes.
