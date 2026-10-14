# Create your Google OAuth client

CleanSync needs its own Google OAuth client to read and write your Drive and, if you set up mail backup, to read and manage your Gmail. Google treats full Drive access as a "restricted" scope, so a shared client would need Google's paid review; using your own client avoids that. This takes about 10 minutes.

1. Go to https://console.cloud.google.com/ and create a new project (for example "drivesync").
2. **APIs & Services → Library** → search "Google Drive API" → **Enable**.
3. **APIs & Services → OAuth consent screen** (also called "Google Auth Platform → Branding/Audience"):
   - User type: **External** (or Internal if you use Google Workspace and only connect accounts from it).
   - App name: CleanSync. Support and developer email: your email.
   - Scopes: add `https://www.googleapis.com/auth/drive`.
   - **Publishing status: click "Publish app" (In production).** In "Testing" mode Google expires the login every 7 days and CleanSync will keep asking you to reconnect.
   - Google shows "this app isn't verified" when you sign in. That is expected for a personal client: click **Advanced → Go to CleanSync (unsafe)**. You are the developer of this app.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - Authorized redirect URI: `<PUBLIC_URL>/api/oauth/callback`, for example `https://drivesync.example.com/api/oauth/callback`. It must match `PUBLIC_URL` exactly.
5. Copy the client ID and secret into `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` and restart CleanSync.
6. For mail backup, also enable the Gmail API and add the `gmail.modify` scope; for **Allow permanent delete** (optional) also add `https://mail.google.com/`. For **Allow Gmail filters** (optional, used by [Unsubscribe](mail-unsubscribe.md)) also add `https://www.googleapis.com/auth/gmail.settings.basic`: it lets CleanSync create and delete Gmail filters, only after you turn on Allow Gmail filters. See [Setting up mail backup](mail-backup.md).

## Branding page (needed to publish)

Google won't publish the app until its Branding page is filled in. CleanSync serves the two
pages Google asks for, with no login: `/privacy` and `/terms`. Set `CONTACT_EMAIL` so they show
your address.

| Field | Value |
|---|---|
| App name | CleanSync |
| User support email | your email |
| App logo | `frontend/public/favicon.svg` turned into a 120 × 120 px PNG (optional; Google only shows it after verification) |
| Application home page | `https://<your address>/` |
| Privacy policy link | `https://<your address>/privacy` |
| Terms of service link | `https://<your address>/terms` |
| Authorized domains | the domain your address is on (e.g. `example.com`) |
| Developer contact email | your email |

Google may ask you to prove you own the domain: verify it in Google Search Console with a
DNS TXT record.

## No public HTTPS address?
Google only redirects to `https://` addresses or `http://localhost`. Options:
- Put CleanSync behind your reverse proxy with a certificate, or use Tailscale/Cloudflare Tunnel.
- Or connect accounts through an SSH tunnel: set `PUBLIC_URL=http://localhost:8080`, run `ssh -L 8080:localhost:8080 your-server`, and open http://localhost:8080 on your computer while connecting.
