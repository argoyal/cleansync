# Changelog

## 0.5.4 — 2026-11-13

A new logo.

- **New logo.** CleanSync has a new logo: in the app, as the browser tab icon, on the privacy and terms pages and at the top of the README. Adding CleanSync to an iPhone or iPad home screen uses it too.
- Upgrading: nothing to do. No database upgrade.

## 0.5.3 — 2026-10-24

Fixes CleanSync slowing down and going unhealthy while background jobs run.

- **Pages no longer pile up behind background jobs.** Every request saved the time the login was last used, so every page and search had to wait for the database while backups, sorting or unsubscribe were writing. The wait used up the database connections and the app went unhealthy ("QueuePool limit of size 5 overflow 10 reached"). The time is now saved at most once a minute.
- **Open tabs no longer hold a database connection.** The live-updates connection of each open tab kept one until the tab closed.
- **`DATABASE_WAL=1`** (new, off by default): lets pages and searches keep loading while background jobs write. Set it only when the data folder is on a local disk; on a network share or a Windows drive it can corrupt the database.
- Upgrading: nothing to do. No database upgrade.

## 0.5.2 — 2026-10-14

Unsubscribe, account photos, and quicker big offloads.

- **Unsubscribe** (Mail → CleanSync → Unsubscribe). Lists every sender whose mail has an unsubscribe link, with how many mails, how many you read and when the last came. Unsubscribe from one or many at once: CleanSync sends the one-click request when the sender says it takes one, or opens their link, or fills a simple form and has your model check the page. Senders it can't reach can be kept out of your Inbox with a Gmail filter (**Allow Gmail filters** in Settings → Accounts, off by default). Old mail is scanned once in the background. See docs/mail-unsubscribe.md.
- **Mail search** has `sender:` for one address exactly (`sender:news@shop.com` leaves out technews@shop.com). Offload and Delete forever on the Unsubscribe page use it.
- **Account photos.** Each mailbox shows its Google profile picture in Settings → Accounts and the account switcher.
- **Big offloads start quickly.** The preview and Start no longer check every mail's file on disk, which could take minutes on Docker Desktop or a network disk and run into a proxy's time limit. Each mail is still fully checked right before it leaves Gmail.
- **Settings.** "Mails per offload batch" is gone; it wasn't used since 0.5.0 (offload works 1,000 at a time).
- Upgrading: database upgrade 0014 runs by itself on start. To use Allow Gmail filters, add the `gmail.settings.basic` scope to your Google consent screen first (docs/google-oauth-setup.md).

## 0.5.1 — 2026-10-13

Fixes after 0.5.0.

- **Running rules.**
  - The Run rules window no longer counts picks from a rule you deleted meanwhile; it can't show "Apply 0 changes" or start a run that does nothing.
  - Approving a run that a newer one replaced says so.
  - Asking for two kinds of pick at once, or approving with nothing picked, is refused with a plain reason.
  - Run waits for the Sorting status before deciding whether a model is needed.
  - Pick all shows ticked when every match is ticked by hand.
- **Correct…** to a Delete forever rule counts the sender's approval once, not twice.
- **Delete forever.**
  - The Activity record groups a rule's deletions from the last one, not the first.
  - From the reader, the progress and result stay on screen after the mail disappears from the list.
  - When stopped, it shows the server's own words.
- **Offload.**
  - When a stop or restart left the last batch's result unknown, the page no longer claims those mails stayed in Gmail.
  - A mail sent again after a restart that fails its copy check says it's already out of Gmail and its copy is kept.
  - Offload on one mail that's no longer in Gmail is refused before anything changes.
- **Allow permanent delete.** Turn off also cancels a Google sign-in you started earlier for Turn on.
- **Restore.**
  - Restore from Offloaded or Deleted in Gmail honours a label filter, and is refused from the Gmail views.
  - If Gmail refuses a restored mail's labels, it comes back with the basic ones (Inbox, Unread, Starred, Important).
- **Smaller things.**
  - An old rule saved with Delete forever and Automatic opens as Suggest first.
  - A rule action still called "delete" shows as Offload.
  - The search options form keeps a bad Size or Age where you typed it, and warns about an unclosed quote.
  - An unknown mail view in a link is refused instead of guessed.

## 0.5.0 — 2026-10-13

Mail now looks like the mailbox, rules run by hand on what you choose, and offload works in bulk.

- **The Mail panel matches Gmail.**
  - The top group shows Gmail's own views (Inbox, Starred, Important, Sent, the new **Archived**, All mail) and your labels, with only mail that is in Gmail.
  - CleanSync's own sections sit below: Suggested, Rules, Offloaded and Deleted in Gmail.
- **Search options.** A form builds the search for you (From, To, words, label, size, date, attachment, unread, starred) and reads the search box back into its fields. Searches now stay in the view you are in.
- **Pick all.** One tick picks every mail matching the view and search, not just the rows on screen.
- **Offload and Delete forever replace Delete.**
  - **Offload** takes mail out of Gmail and keeps CleanSync's copy.
  - **Delete forever** removes it everywhere, after you type the count.
  - The old Local archive is now part of Offloaded, with nothing lost on upgrade.
  - The rule action Delete is now Offload, and old rule files still load.
- **Deleted in Gmail** gets Delete forever. This fixes the "Pick between 1 and 500 mails" error.
- **Running rules by hand.**
  - **Run** on a rule card runs that one rule over the Inbox.
  - **Run rules** on a search or a pick lets you tick which rules to run.
  - Either way you see the list of matched mails, untick any you want left alone, and approve. The count is typed only for Offload and Delete forever.
  - "Run rules on…" is gone. Closing the window keeps the run going.
  - Mail you restore or take back yourself isn't offloaded again by a run.
- **Offload in bulk.**
  - Mail is removed from Gmail up to 1,000 at a time, after each copy is checked.
  - Offloads have **Stop**, carry on after a restart, and wait (retrying after 1, 5, 15, then 60 minutes) if Gmail doesn't answer.
- **Allow permanent delete** (Settings → Accounts, per mailbox, off by default).
  - With it on, Offload deletes mail from Gmail directly, so the space comes back at once. It needs full Gmail access, which you approve once in Google's sign-in.
  - It also allows Delete forever on mail still in Gmail and as a rule action. Delete forever rules always wait for your approval and the typed count.
  - Losing the permission turns it off; only **Turn on** turns it back on.
- Restored mail comes back where it was, Inbox included.

## 0.4.0 — 2026-10-13

- Added: a model connection now pauses when the provider says its limit is used up, until the time the provider gave or the connection's **Daily limit resets at** time (with its time zone). The pause is saved and survives a restart. **Resume now** ends it, and Settings → Models shows the server's own message (Test shows it too).
- Changed: a 429 reply is no longer retried at once. A limit error no longer tries every answer format. An address ending in `/chat/completions` is shortened to the part before it. A 405 reply now explains that the address usually ends in `/v1`.

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
