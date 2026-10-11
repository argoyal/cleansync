# Setting up mail backup

CleanSync keeps a copy of every Gmail message on your server, as the original `.eml` file, and keeps it up to date every few minutes. It can then offload old or large mail out of Gmail, and put it back when you want. It never sends mail.

## 1. Let your Google client read mail

CleanSync uses your own Google OAuth client (see [Google OAuth setup](google-oauth-setup.md)). Mail needs two more things there:

1. In the Google Cloud console, open **APIs & Services → Library**, find **Gmail API** and click **Enable**.
2. Open **Google Auth Platform → Data access** (or **OAuth consent screen → Scopes**), click **Add or remove scopes**, and add `https://www.googleapis.com/auth/gmail.modify`. Save.

`gmail.modify` lets CleanSync read your mail, move offloaded mail to Gmail's trash, take it out again, and put mail back (import). It does not let CleanSync send mail. It also cannot empty Gmail's trash: that would need Google's full mailbox permission, which CleanSync deliberately does not ask for.

## 2. Reconnect with Gmail ticked

In CleanSync, open **Mail** in the rail and press **Set up mail backup** (or **Reconnect mail** if it says so). Google asks for permission again; tick the box for Gmail and continue. Drive keeps syncing meanwhile, and if you cancel, nothing changes. If you leave the Gmail box unticked, Mail stays "Set up" and Drive works as before.

## 3. Start

CleanSync counts your mail and shows roughly how many mails and how much space ("about N mails, about X GB"; the size is what Gmail and Google Photos use together, as Google doesn't report Gmail alone). Press **Start**. The first backup takes a few hours for a big mailbox; it picks up where it stopped after a restart, a network problem, a missing disk or a paused budget. Nothing in Gmail changes.

## What is kept, and where

- All mail except Spam and Trash. Drafts and chats are left out.
- Each mail once, in `mail/<email>/<year>/<month>/<gmail-id>.eml` next to `storage/` in CleanSync's folder (never inside a Drive sync folder, so mail is never uploaded to Drive). Labels are kept and shown as folders.
- Mail you delete in Gmail stays here, marked "Deleted in Gmail", and can be put back.

## Reading

The Mail section shows your mail by view (All mail, Inbox, Sent, Starred, Important, Offloaded, Deleted in Gmail) and by label, grouped by conversation. Search takes plain words and these: `from:`, `to:`, `label:`, `has:attachment`, `larger:5M`, `smaller:1M`, `before:2024-01-31`, `after:2024-01-01`, `older_than:1y`, `newer_than:30d`, `is:unread`, `is:starred`, `is:offloaded`, `is:deleted`. Mail is shown safely: scripts never run, and images from the internet only load when you press **Show images**.

## Offloading mail

**Offload mail…** builds a filter (older than, from, to, label, larger than, has attachments), or starts from your current search, or from mails you picked. The preview shows how many mails, how much space, the largest and oldest, and what it leaves out and why: mail newer than 30 days (Settings → Mail), mail not fully backed up, and mail already offloaded or deleted. Type the number to start.

For each mail CleanSync first checks its copy here (there, same size, reads as a mail), then moves it to Gmail's trash, then checks it is there. A mail that fails a check stays in Gmail and is listed on the offload.

**Offloaded mail sits in Gmail's trash. Gmail empties its trash on its own after 30 days; that's when the space comes back.** There is no button to empty it sooner, because that needs a wider Google permission than CleanSync asks for. If you need the space right away, empty Gmail's trash yourself in Gmail, but only after checking the offload is what you expect: mail removed from the trash is gone from Gmail for good, and your copy here is then the only one.

Offloads wait while the mail backup is still checking your mailbox (the first backup, or a full re-check): CleanSync says "Wait until the mail backup has finished checking your mailbox, then try again." Try again when the check is done.

## Putting mail back

**Restore to Gmail** works on one mail, a conversation, a search, a whole offload, or everything deleted in Gmail (Recovery → Mail). A mail still in Gmail's trash is taken out of it; a mail Gmail no longer has is imported from its stored `.eml` with its labels; a mail already in Gmail is left as it is. Restore does not make a second copy.

## Cost

The Gmail API is free today; Google plans to charge above 80 million units a day per Google Cloud project. CleanSync paces its calls and keeps to its own daily budget: 10 million units by default, at most 40 million (Settings → Google API, where you can see today's use). Drive calls are counted in the same total but are never held back. When the budget is used up, mail backup and offloads wait until midnight UTC; Drive syncing goes on.

## Limits to know about

- Mails that cannot be downloaded from Gmail ("failed" mails) are never offloaded. They are also not counted in the preview's left-out list.
- A mail with no usable Message-ID header that is restored after Gmail's answer was lost could end up twice in Gmail. This is rare; CleanSync looks for the mail by Message-ID before importing.
- Drafts, chats, Spam and Trash are not backed up.
- Mail deleted in Gmail stays here, marked "Deleted in Gmail"; CleanSync never deletes its own copy.

See also [troubleshooting](troubleshooting.md) and [known limitations](limitations.md).
