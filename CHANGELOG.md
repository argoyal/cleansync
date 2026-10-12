# Changelog

## 0.3.1 — 2026-10-12

- Fix: starting a new "Run rules on…" right after closing a preview that was still deciding could leave a mail out of the new run, and turning sorting off while a mail is being sorted no longer sorts it.

## 0.3.0 — 2026-10-12

- Mail rules: write rules in your own words, and a model you choose (any OpenAI-style server: Ollama, LM Studio, a LiteLLM gateway, OpenRouter…) sorts new inbox mail by them. Set up the model in Settings → Models (with Test); write rules from a sentence or from picked mail in Mail → Rules, with fixed conditions, Try it, starter rules, export/import and copy. Rules suggest first (Suggested view, rule chips with Approve, Correct… and Undo) or act automatically; senders you approve three times are remembered. "Run rules on…" previews a run on older mail and changes it after you type the count. Every change is logged and can be undone. See docs/mail-rules.md.
- Delete and the Local archive: Delete (by hand or by a rule) checks CleanSync's copy, then moves the mail to Gmail's trash; CleanSync keeps the copy in a Local archive, apart from the other views and search. Restore it to Gmail, or Delete forever… with a typed count.
- Model API keys and extra headers are kept only as mode-600 files, never returned and redacted in logs. Rejected requests (HTTP 422) no longer repeat the submitted values.
- Upgrading: Database upgrade 0009 runs by itself on start; it adds the tables for rules and decisions. Mail sorting stays off until you set up a model, add rules and turn on "Sort new inbox mail" for a mailbox.
- Fix: Delete forever refreshes the "N mails · size" header and its count at once, and is not offered when a label filter is set.

## 0.2.1 — 2026-10-12

- Mail opens quickly on big mailboxes. The list reads only the conversations it shows, label views and counts use a table of their own, and the "N mails · size" total loads separately, so the list doesn't wait for it.
- A running mail backup no longer freezes the app. Each mail is saved in one short step, away from the part of the server that answers pages.
- During a backup the progress refreshes as it goes; the lists and counts catch up once a minute, and again when the backup job ends.
- Database upgrade 0008 runs by itself on start. It adds the label table and new indexes, and takes a few seconds on a big mailbox.

## 0.2.0 — 2026-10-11

- DriveSync is now called CleanSync. The data folder, `.drivesync-volume` and the environment variables keep their names, so an existing installation upgrades in place. The image is `ghcr.io/argoyal/cleansync`.
- Public `/privacy` and `/terms` pages for Google's consent screen (Branding page). Their contact address comes from the new `CONTACT_EMAIL` setting.
- Upgrading: the example compose service is renamed from `drivesync` to `cleansync`. Run `docker compose up -d --remove-orphans` once when you upgrade, so the old `drivesync` container goes away.
- The + New button and uploads are hidden in 0.2.0 (one switch; the code stays and comes back later). "Sync a Drive folder…" moved to the Files panel.
- New navigation: an icon rail (Files, Mail, Activity, Recovery, Settings) with a panel for each section; a bottom bar on phones.
- Mail backup: every Gmail message except Spam and Trash is kept on the server as its original .eml, kept up to date every few minutes; the first backup resumes after any stop. Set it up from the new Mail section (docs/mail-backup.md); it needs the Gmail API and the `gmail.modify` scope on your Google client.
- Mail reader: views, labels, conversations, search with operators, safe display (no scripts, images on request), attachments.
- Mail offload: by filter or by pick, with a preview and a typed count; each mail is checked here before it goes to Gmail's trash, and Gmail empties its trash after 30 days. Offloads wait while the backup is checking the mailbox. Restore to Gmail takes mail out of Gmail's trash or imports it, and never imports a mail twice, even after Gmail's answer got lost; the one exception is mail with no usable Message-ID (missing, or with characters a Gmail search can't take), which could rarely end up twice. Recovery shows mail too.
- Cost guard: Google API calls are paced and kept under a daily budget (default 10 million units, at most 40 million; Settings → Google API). Drive is counted but never held back.
- Fix: a sync that fails or is cut off no longer leaves its folder "syncing right now".

## 0.1.0 — 2026-10-09
- Backend core: login, Google account connection, sync pairs, first-sync preview and confirmation, scheduled and watched two-way sync with deletion limit, conflicts, history, logs and live events.
- Web UI modelled on Google Drive: file browser with sync status per item, open/download/zip, previews, uploads, new folder/rename/move/delete (deletes go to Recovery first), + New with Office and Google files, first-run wizard, sync activity, blocked-sync review, conflicts, settings and storage.
- Cached Drive listing per sync pair, security headers on every response.
- Syncing stops for every pair when the storage disk is missing (checked at startup, before every sync and before every file action; one failed read is retried a second later), with one clear message in the app. While stopped, the Sync switches say so, and turning a pair on or adding one is refused.
- Each sync pair has a Sync on / off switch. A new pair's local folder follows its Drive folder. Theme switch: System, Light or Dark.
- Add folders to sync from My Drive: New → "Sync a Drive folder…", and right-click a local folder → "Start syncing this folder". Synced folders and not-synced folders have their own marks.
- Offload: keep files on the server and remove them from Google Drive into Drive's trash, checked file by file before and after; Restore to Drive; Empty from trash now. Right-click (one or several items) or the Offloaded page. Google Docs, Sheets and Slides are skipped and stay on Drive.
- Recovery page: everything DriveSync kept, restore one or many without overwriting, download one or many (as a zip). "Show in Recovery" in the right-click menu.
- New and Upload are off outside synced folders; local-only items there say "Not synced".
- Every + New item is checked end to end in the built image. Google Docs, Sheets and Slides on a pretend local account say they need a real Google account.
