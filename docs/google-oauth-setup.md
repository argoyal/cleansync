# Create your Google OAuth client

DriveSync needs its own Google OAuth client to read and write your Drive. Google treats full Drive access as a "restricted" scope, so a shared client would need Google's paid review; using your own client avoids that. This takes about 10 minutes.

1. Go to https://console.cloud.google.com/ and create a new project (for example "drivesync").
2. **APIs & Services → Library** → search "Google Drive API" → **Enable**.
3. **APIs & Services → OAuth consent screen** (also called "Google Auth Platform → Branding/Audience"):
   - User type: **External** (or Internal if you use Google Workspace and only connect accounts from it).
   - App name: DriveSync. Support and developer email: your email.
   - Scopes: add `https://www.googleapis.com/auth/drive`.
   - **Publishing status: click "Publish app" (In production).** In "Testing" mode Google expires the login every 7 days and DriveSync will keep asking you to reconnect.
   - Google shows "this app isn't verified" when you sign in. That is expected for a personal client: click **Advanced → Go to DriveSync (unsafe)**. You are the developer of this app.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - Authorized redirect URI: `<PUBLIC_URL>/api/oauth/callback`, for example `https://drivesync.example.com/api/oauth/callback`. It must match `PUBLIC_URL` exactly.
5. Copy the client ID and secret into `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` and restart DriveSync.

## No public HTTPS address?
Google only redirects to `https://` addresses or `http://localhost`. Options:
- Put DriveSync behind your reverse proxy with a certificate, or use Tailscale/Cloudflare Tunnel.
- Or connect accounts through an SSH tunnel: set `PUBLIC_URL=http://localhost:8080`, run `ssh -L 8080:localhost:8080 your-server`, and open http://localhost:8080 on your computer while connecting.
