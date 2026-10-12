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
- **HTTP headers**: every response has `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` and a Content-Security-Policy that allows only CleanSync's own scripts, styles, fonts and connections (the API docs page also allows jsDelivr, which serves Swagger). Put HSTS on your reverse proxy.
- **New Google Docs**: to create one, CleanSync reads the saved refresh token from the encrypted `rclone.conf`, exchanges it with Google for an access token, and keeps that only in memory until shortly before it expires. Neither token is logged or sent to the browser.
- **Files you open**: files are served from CleanSync's own address, so a file must never be able to act as you. HTML, XML, JavaScript and other text files are sent as plain text, so they show their source and never run as a page. Images (SVG too) are shown inline under a sandbox rule that blocks scripts, forms and access to your login. Video, audio and PDF are shown inline without that sandbox, so seeking and the PDF viewer work; they only get `frame-ancestors 'none'` and `nosniff`. Office files and unknown types are downloaded instead of shown.
- **Mail you open**: HTML mail is cleaned on the server with an allow-list (nh3): scripts, event handlers, frames, forms and plugins are removed, and links keep only http, https and mailto. The mail is then shown in a sandboxed frame with its own Content-Security-Policy: no script runs, it has no access to CleanSync's cookies or pages, and images from the internet load only after you press "Show images" for that mail. Images inside the mail are taken from the mail itself. Attachments open like files: PDFs, images and plain text preview; anything that could run script is sent as text or downloaded.
- **AI model keys**: the API key and any extra header values of a model connection are stored only as files under `app/data/secrets/ai/`, file mode 600. They are never stored in the database, never sent back to the browser (the app only says a key is saved), and are redacted from logs.
- **Mail and the AI model**: to sort a mail, CleanSync sends the model's server the sender, recipients, subject, date, attachment names and up to 2,000 characters of the mail's text (links reduced to their domain), read from CleanSync's own copy. A hosted model's company sees that text; use a model on your own machine if that matters. The mail is marked as data, not instructions, and the model's answer can only pick one of your rules: an answer that can't be read, or names a rule that isn't on the list, means "no rule". The model never sends mail and can't remove mail from Gmail for good. See [mail rules](mail-rules.md).
- **Rejected requests**: when a request is refused because a field is wrong (HTTP 422), the answer says which field and why, but never repeats the value that was sent, so a key or password typed in the wrong place doesn't come back.
- **Thumbnails** are made by restricted image decoders: only a few common image types are accepted, and anything oversized or unreadable simply gets no thumbnail.
- **Uploads** are streamed to a temporary file and moved into place only when complete. An upload never overwrites an existing file; it gets a free name instead.
- **Pretend Google account** (for development and tests only): an account whose "Drive" is a folder on the same machine. Adding one is refused unless `DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1` is set; an account added that way keeps working afterwards. Never set it in production.

## Running behind a reverse proxy
The lockout counts failures per client address. Behind a proxy, every request seems to come from the proxy, so CleanSync reads the real address from the `X-Forwarded-For` header, but only when the request comes from an address listed in `FORWARDED_ALLOW_IPS`. Set it to your reverse proxy's address, for example the Docker network gateway. The default is `127.0.0.1`, which trusts nobody outside the container.

Do not set it to `*`. Anyone who can reach the port directly could then invent an address in that header and dodge the lockout.

## Filesystem permissions
The mounted folder (for example `/srv/drivesync`) must be readable and writable by uid/gid 1000. CleanSync creates `app/data` and `storage` inside it.

## Reporting a problem
See [SECURITY.md](../SECURITY.md).
