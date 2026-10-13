# Offload in bulk and Delete forever in Gmail (0.5.0, round 3 of 3)

Status: draft for review · 2026-10-13 · builds on [mail panel and search](2026-10-13-mail-panel-and-search-design.md) and [running rules](2026-10-13-running-rules-design.md). Closes issue #18.

## 1. Why

Today an offload moves mail to Gmail's trash **one mail at a time**:
1. it checks the copy;
2. it trashes the mail;
3. it asks Gmail whether the mail landed in the trash.

That is three calls per mail and slow for big offloads. The space only comes back when Gmail empties its trash, 30 days later. CleanSync already checks its own copy before removing anything, so the owner doesn't need Gmail's trash as a second safety net.

The owner's decisions:
- **Offload removes mail from Gmail in bulk, permanently,** with no trash.
- **Full Gmail access is opt-in per mailbox (option A).** Without it, Offload still works in bulk but uses Gmail's trash.
- **Delete forever** (mail gone everywhere) works on mail still in Gmail, and is a rule action, for mailboxes that allow it.

## 2. What success looks like

- **Bulk offload.** A 10,000-mail offload on a mailbox that allows permanent delete takes about 10 Gmail calls to remove the mail, not 30,000. The space comes back at once.
- **Opt in, nothing breaks.** Mailboxes that haven't opted in keep working: Offload moves mail to the trash in bulk.
- **Same safety step.** No mail is removed from Gmail before its copy here has been checked. That's today's check: the file can be read, and its size and checksum match.
- **Settings → Accounts makes it clear** which mailboxes allow permanent delete, and how to turn it on or off.

## 3. The permission

- **New scope:** `https://mail.google.com/` ("Read, compose, send and permanently delete all your email from Gmail"). CleanSync keeps asking for `gmail.modify` too.
- **Settings → Accounts → each mailbox** gets **Allow permanent delete**:
  - **Off** (default after the upgrade): a short line, "Offload moves mail to Gmail's trash. Turn this on to delete it from Gmail directly, so the space comes back at once." and **Turn on**.
  - **Turn on** starts the usual Google sign-in for that mailbox, asking for the extra scope. When Google grants it, the switch shows **On**.
  - **On**: "Offload and Delete forever remove mail from Gmail directly. Gmail's trash isn't used." and **Turn off**.
  - **Turn off** stops CleanSync from using the permission. It doesn't revoke it at Google; the line says how to remove it in the Google account settings.
- **Whether a mailbox allows it** comes from the scopes Google granted, which CleanSync already saves (`granted_scopes`), plus the owner's switch. The switch is saved per account (`accounts.permanent_delete`, migration 0013). It is on only when the switch is on **and** the scope is granted.
- **If Google later refuses the scope** (for example, the owner removed it at Google), CleanSync turns the switch off for that mailbox. It shows "Permanent delete was turned off: Google no longer allows it for this mailbox." and goes back to trashing.
- **The setup guide** (`docs/google-oauth-setup.md` and `docs/mail-backup.md`) adds the scope to the consent screen steps, with a plain warning about what it allows.

## 4. Offload in bulk

An offload works through its mails in **batches of up to 1,000**. For each batch:

1. **Check each copy here,** as today. Mail whose copy fails is skipped, with today's reason, and stays in Gmail. Only checked mail goes on.
2. **Remove the checked mail from Gmail in one call:**
   - **Permanent delete allowed:** `users.messages.batchDelete` with the ids. On success, every mail in the batch is **offloaded**, marked "removed from Gmail".
   - **Not allowed:** `users.messages.batchModify` with `addLabelIds: ["TRASH"]`. On success, every mail in the batch is **offloaded**, marked "in Gmail's trash", as today. There's no per-mail check that it reached the trash: Gmail's answer to the batch call is the check.
3. **If the batch call fails:**
   - a temporary error (5xx, 429, network) is retried after a pause, as Gmail calls are today;
   - any other error splits the batch: CleanSync falls back to today's one-at-a-time path (`trash`, or `messages.delete` with permanent delete), so one bad id can't block a thousand good ones. Each mail that still fails is skipped, with Gmail's message.
4. **Progress, Stop and the resume after a restart work as today,** per batch. A batch is written as done only after Gmail's success answer. After a restart, a batch that was sent but not recorded is sent again. Deleting an already-deleted id fails quietly in Gmail's batch call, and the fallback treats "not found" as already removed.

**Quota.** `batchDelete` and `batchModify` cost 50 units per call, compared with 5 to 10 per mail today. They are counted in the existing daily budget.

**The same path for everything.** A rule's Offload action and Offload on one mail use the same choice: `messages.delete` when permanent delete is allowed, `messages.trash` otherwise. Round 1 already routes them through the applier.

## 5. Delete forever on mail in Gmail

- **Where it's offered:** in the Gmail views, on picked mail (by hand or Pick all) and on a mail open in the reader, but **only for mailboxes that allow permanent delete.** For other mailboxes the button is shown disabled: "Turn on Allow permanent delete in Settings → Accounts."
- **What it does,** after the typed count:
  1. remove the mail from Gmail (`batchDelete` in batches of 1,000);
  2. for each mail Gmail removed, remove CleanSync's copy (the `.eml`, the search entry, the rule history and the row);
  3. write the Activity record, as today's Delete forever does.
  
  If step 1 fails for a mail, its copy is kept and the mail is listed as not deleted. CleanSync never removes its copy of mail that is still in Gmail.
- **It runs as a background job** with progress and Stop, like an offload. It uses the offload job machinery, with its own kind "delete forever".
- **In Offloaded and Deleted in Gmail** it works as in round 1: only the copy is removed, because the mail is already gone from Gmail.

## 6. Delete forever as a rule action

- **The action can be picked** in the rule form once the mailbox allows permanent delete. Otherwise it stays disabled, with the Settings hint.
- **A rule with Delete forever:**
  - is always **Suggest first**: the form disables Automatic, and the server refuses it;
  - stands alone, like Offload.
- **When it's applied** (on approval in Suggested, or in a run's match list), the mail is deleted forever as in section 5. A run's approval needs the typed count of those mails (round 2).
- **Undo isn't offered** on a Delete forever decision, because nothing is left to undo. The chip says "deleted forever".
- **If the mailbox stops allowing permanent delete,** its Delete forever rules stop applying. Their decisions wait with the reason "Turn on Allow permanent delete in Settings → Accounts." and the rule card shows the same warning.

## 7. What the owner sees about space

- **Offloads with permanent delete** say "Removed from Gmail. The space is free now." in place of "In Gmail's trash until …".
- **Mixed offloads** (some batches trashed before the switch was turned on) show both lines with counts.
- **The Storage page's Gmail figure** updates on its next refresh, as today.

## 8. Restore after a permanent delete

Restore to Gmail already handles "Gmail no longer has it": it imports the mail again from its stored `.eml`, with its labels, after checking by Message-ID that it isn't there already. Nothing new is needed. The restore summary says "imported again" for these mails.

## 9. The backup must not mistake it

When the backup's keep-up step sees that an offloaded mail is gone from Gmail, the mail stays **offloaded**. That is today's rule for mail Gmail emptied from its trash, and it covers permanent deletes too. A test pins it. Mail deleted forever has no row left, so there is nothing to see.

## 10. Out of scope

- Emptying Gmail's trash for offloads made before this release.
- Revoking the permission at Google from inside CleanSync.
- Permanent delete for Drive files.

## 11. Testing

**Backend:**
- The Gmail adapter gets `batch_delete(ids)`, `batch_trash(ids)` and `delete(id)`, tested against the fake Gmail and with respx for the real request shape.
- Offload, with and without permission:
  - batches of 1,000;
  - a copy that fails is skipped and never sent;
  - a batch error falls back to one at a time;
  - a 429 waits;
  - a restart mid-batch resends without double counting;
  - quota is counted per call.
- The permission:
  - the switch needs the scope;
  - losing the scope at Google turns the switch off;
  - a reconnect asks for the scope only when turning it on.
- Delete forever on mail in Gmail: Gmail first, then the copy; a failed Gmail delete keeps the copy; the Activity record is written; it is refused without permission.
- The rule action:
  - can't be saved without permission;
  - can't be Automatic;
  - is applied on approval;
  - has no Undo;
  - waits with its reason if the permission is turned off.
- Keep-up leaves a permanently deleted offloaded mail offloaded.
- Migration 0013.

**Frontend (vitest):**
- The Accounts switch and its texts.
- Delete forever enabled or disabled by permission.
- The rule form's Delete forever, with Automatic disabled for it.
- The offload space texts.

**Playwright:** with the fake Gmail, offload a search with permanent delete on, see "Removed from Gmail", then restore. CI's fake Gmail needs `batchDelete` and `batchModify`.

**Docs:**
- `docs/google-oauth-setup.md`, `docs/mail-backup.md` (offload and Delete forever) and `docs/mail-rules.md` (the Delete forever action);
- `docs/security.md`: what full Gmail access allows, and that it is opt-in;
- `docs/limitations.md`.
