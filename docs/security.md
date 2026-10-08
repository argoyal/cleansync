# Security

- **Login**: one admin user. Passwords are hashed with argon2id, and the number of hashes running at once is capped so a flood of login attempts cannot exhaust memory. The first login forces a new password (12+ characters).
- **Lockout**: 5 wrong passwords within 15 minutes lock that username, from that client address, for 15 minutes.
- **Sessions**: random id in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` when `PUBLIC_URL` is https). Idle timeout 12 hours by default. Changing the password rotates the session: every other session is ended and a new one is issued.
- **CSRF**: every request that changes something also needs the `X-CSRF-Token` header (only the login request is exempt).
- **Google logins** are stored only in `app/data/rclone/rclone.conf`, encrypted with rclone's config encryption (`RCLONE_CONFIG_PASS`), file mode 600. The file is encrypted again at startup if it was ever found in plain text. Logins are never sent to the browser or stored in the database.
- **No secrets in logs**: tokens, passwords and client secrets are redacted from log files, job logs and the activity feed.
- **Paths** from the browser are resolved and must stay inside the account's `storage/<email>/` folder. `..`, absolute paths, NUL bytes and symlinks pointing outside are refused.
- **Commands**: rclone runs without a shell. Its arguments are built only in `backend/app/services/rclone/commands.py`, which checks them (for example, every command that can delete must use Drive's trash). The UI cannot pass rclone flags.
- **OAuth callback** works without the session cookie (Google's redirect is cross-site), so it is protected by a single-use state value that expires after 10 minutes, plus PKCE.
- **Container**: runs as user 1000, read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`.

## Running behind a reverse proxy
The lockout counts failures per client address. Behind a proxy, every request seems to come from the proxy, so DriveSync reads the real address from the `X-Forwarded-For` header, but only when the request comes from an address listed in `FORWARDED_ALLOW_IPS`. Set it to your reverse proxy's address, for example the Docker network gateway. The default is `127.0.0.1`, which trusts nobody outside the container.

Do not set it to `*`. Anyone who can reach the port directly could then invent an address in that header and dodge the lockout.

## Filesystem permissions
The mounted folder (for example `/srv/drivesync`) must be readable and writable by uid/gid 1000. DriveSync creates `app/data` and `storage` inside it.

## Reporting a problem
See [SECURITY.md](../SECURITY.md).
