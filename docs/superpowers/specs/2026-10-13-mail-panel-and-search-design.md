# Mail panel, search filters, select all, Offload and Delete forever (0.5.0, round 1 of 3)

Status: draft for review · 2026-10-13

0.5.0 ships three rounds together:
1. **This spec:** the mail panel, search filters, select all, and one meaning for "removed from Gmail".
2. **Running rules:** Run on each rule card, Run rules with a rule picker on a search, approval with select all.
3. **Offload in bulk:** permanent delete in Gmail with the full-access permission, and Delete forever for mail still in Gmail.

## 1. Why

The owner's goals:
- **The Mail section should look like the mailbox.** Gmail's folders and labels, as they are, at the top. Everything CleanSync adds goes in its own area below.
- **Searching shouldn't mean remembering keywords.** A filter form should build the search.
- **A whole search result should be pickable with one tick,** without ticking mail one by one.
- **"Removed from Gmail, copy kept" should be one idea.** Today it is two ("Offloaded" and the "Local archive") that work slightly differently, and the button is called "Delete" in some places and "Offload" in others.
- **Fix the bug** where Delete in the **Deleted in Gmail** view says "Pick between 1 and 500 mails to delete": the view's mails aren't in Gmail, so nothing is found to delete.
- **Archived mail should be findable.** A mail a rule archived is in All mail, but there is no view for it.

## 2. What success looks like

- The left panel has two clearly separate groups. The Gmail group shows only mail that is in Gmail.
- A search can be built entirely from a form, and the form and the search box always agree.
- One tick picks every mail matching the current view and search. Offload, Delete forever (where it's available), Restore and Make a rule act on all of them.
- Nothing in the app is called "Delete". Mail is **Offloaded** (out of Gmail, copy kept) or **Deleted forever** (gone everywhere).
- Upgrading moves mail now in the Local archive into Offloaded, with nothing lost.

## 3. Words used in the app

| Word | Meaning |
|---|---|
| **Archive** | Gmail's archive: the mail leaves the Inbox and stays in Gmail. |
| **Offload** | CleanSync checks its copy, removes the mail from Gmail and keeps the copy. It can be restored to Gmail later. Before round 3 the mail goes to Gmail's trash; round 3 deletes it from Gmail directly. |
| **Delete forever** | The mail is removed from Gmail (if it is still there) and CleanSync's copy is removed too. It exists nowhere afterwards. You confirm by typing the count. |
| **Deleted in Gmail** | You deleted the mail yourself in Gmail (for example on your phone). CleanSync still has its copy. |

## 4. The panel

```
GMAIL
  Inbox · Starred · Important · Sent · Archived · All mail
  Labels ▾  (the mailbox's labels)
──────────── CleanSync ────────────
  Suggested · Rules · Offloaded · Deleted in Gmail
```

- **Gmail views show only mail that is in Gmail** (state `IN_MAIL`). That covers Inbox, Starred, Important, Sent, Archived, All mail and the label views.
- Their counts, search results and select all leave out offloaded mail, mail deleted in Gmail, and today's local archive. Today offloaded and deleted mail show in All mail with a mark; that stops.
- **Archived** shows mail in Gmail whose labels include neither `INBOX` nor `SENT`. It is listed by conversation like the other views. Its search key is `in:archived`. A thread shows in Archived when any of its mails is archived, the same way a thread shows in Inbox when any of its mails is in the Inbox.
- **Offloaded** shows every mail CleanSync removed from Gmail, whether by an offload, a rule's Offload action, or Offload on picked mail.
  - It lists mail, with its own search and the filter form.
  - A **Batches** tab shows the offloads started from a filter or a pick, with the space each freed. This is today's offload list.
- **Deleted in Gmail** works as today, plus Delete forever (section 7).
- **Suggested** and **Rules** move into the CleanSync group unchanged.
- The **Local archive** view is removed (section 8).

The panel shows a small label "CleanSync" on the line between the groups. The label fold for Gmail labels stays as it is.

## 5. The search filter form

A filter button (an icon labelled "Search options") sits at the right end of the search box. It opens a form below the box, the way Gmail does:

| Field | Writes |
|---|---|
| From | `from:<value>` |
| To | `to:<value>` |
| Has the words | the plain words |
| Label | `label:<name>`, picked from the mailbox's labels |
| Size | `larger:<n><unit>` or `smaller:<n><unit>`: a choice of larger or smaller, a number, and a unit (KB or MB) |
| Date | a choice of: any time · older than (n days/months/years) → `older_than:` · newer than → `newer_than:` · before a date → `before:` · after a date → `after:` |
| Has attachment | `has:attachment` |
| Unread / Starred | `is:unread` / `is:starred` |

- **Search** writes the keywords into the search box in this order, joined by spaces, then runs the search. **Clear** empties the form. Closing the form changes nothing.
- **Opening the form reads the search box back into the fields.** Each keyword the form knows fills its field. Anything it doesn't know stays in **Has the words**, so nothing typed is lost.
- Values with spaces are quoted (`from:"Example News"`).
- **One parser does the reading.** The backend parser in `search.py` stays the only real one. The frontend gets a small reader for the same keywords, and the search box text is what gets sent.
- **Mistakes come from the server.** If the server rejects a search, its message shows under the box as today. The form doesn't check values itself, except that Size and the numbers must be numbers.
- **Where the form appears:** every mail view, including Offloaded and Deleted in Gmail. Label is hidden in the Deleted in Gmail view, where labels are no longer reliable.

## 6. Select all

- **The checkbox at the top of the list** (labelled "Pick all") has three states:
  - **empty:** nothing picked;
  - **mixed:** some picked by hand;
  - **ticked:** all picked.
- Ticking it picks **every mail matching the current view and search**, not just the rows loaded on screen. The bar then says: "All 12,430 mails in this search are picked." with **Clear**.
- Unticking a single row after a pick-all turns it back into a by-hand pick of the rows on screen minus that one, and the bar says so: "48 threads picked".
- A pick-all is sent to the server as **the view and the search** (`{view, label, q}`), never as a list of ids. The server works out the mails at the moment the action starts.
- **The actions in the bar depend on the view:**

| View | Actions on picked mail |
|---|---|
| Gmail views | **Offload**, **Make a rule…** (from up to 10 of the picked mails), and in round 2 **Run rules** |
| Offloaded | **Restore to Gmail**, **Delete forever** |
| Deleted in Gmail | **Restore to Gmail**, **Delete forever** |
| Suggested | **Approve**, as today |

- **Offload** opens today's offload dialog with the pick as its filter. It shows the count and size, and asks you to type the count. The 500-mail limit of today's Delete goes away: Offload runs as an offload job, in batches.
- **Delete forever** asks for the typed count, as today's Delete forever does.
- **Restore to Gmail** works as today.
- A pick (all or by hand) is cleared when the view, label or search changes, as today.

## 7. Delete forever in the CleanSync views

- In **Offloaded** and **Deleted in Gmail**, Delete forever removes CleanSync's copy: the `.eml`, the search entry, the rule history and the row. The mail is already gone from Gmail.
- It writes the same Activity record as today's Delete forever (up to 500 senders, subjects and dates, plus how many more).
- It refuses while a restore is running for that mailbox, as today.
- It works on a pick by hand or a pick-all. Today's one-off endpoint (`/api/mail/archive/delete-forever`) is generalised to take `{view, label, q}` or `message_ids`, for the views `offloaded` and `deleted`.
- **This fixes the "Pick between 1 and 500" bug:** the Deleted in Gmail view no longer offers the old Delete, which looked only at mail still in Gmail.
- **Delete forever for mail still in Gmail is round 3**, because it needs the full-access Gmail permission.

## 8. One "removed from Gmail" state

**On upgrade (migration 0011):**
- every mail in state `LOCAL_ARCHIVE` moves to `OFFLOADED`;
- the `LOCAL_ARCHIVE` state is no longer used by any code, though the value stays in the enum so old backups still read;
- the full-text search entries need no change.

**Offload by rule or by hand.** A rule's Offload action, and Offload on a mail open in the reader, do what Delete does today, except the mail ends in `OFFLOADED`, not `LOCAL_ARCHIVE`:
1. check the local copy;
2. move the mail to Gmail's trash;
3. set the state.

These mails don't belong to an offload batch. The Batches tab lists only filter and pick offloads, as today.

**Restore to Gmail** uses one path for every offloaded mail. It is today's offload restore: out of Gmail's trash, or imported again if Gmail no longer has it. The separate local-archive restore path is removed.

**Rule actions** become **Archive, Label, Mark read, Star, Offload, Delete forever**:
- The stored action kind `delete` is renamed `offload`. Migration 0011 rewrites the saved rules, rule versions and decision actions.
- YAML import accepts `delete` as another name for `offload`, so old exports still load.
- **Delete forever** is listed but can't be picked until round 3. It shows "Needs full Gmail access (coming in this release)".
- **Offload** and **Delete forever** each stand alone, the way Delete does today: a rule that offloads does nothing else.
- In round 3, a rule with Delete forever can only be in **Suggest first** mode.

**What the reader shows.** Opening a mail from Offloaded shows it read-only, with **Restore to Gmail** and **Delete forever**, as the Local archive does today.

## 9. API changes (for the plan)

- `GET /api/mail/threads` and the counts: the Gmail views filter on `state == IN_MAIL`. The new view `archived` adds `NOT INBOX AND NOT SENT`. The views `offloaded` and `deleted` stay. The view `archive` is removed; the server answers it as `offloaded` so old links still work.
- **Search.** `in:archived` is added. `is:offloaded` and `is:deleted` keep working in their own views. In a Gmail view they give the plain message "Offloaded mail is in the Offloaded view."
- **Pick-all, as `{account_id, view, label, q}`, is accepted by:**
  - offload start, which already takes a filter; the view and label are folded into it;
  - restore, which already takes a `q`; the view is added;
  - the new Delete forever;
  - in round 2, run rules.
- **Removed:** `POST /api/mail/delete`, the old 1-500 Delete. Offload replaces it. The reader's single-mail Delete becomes Offload, through the same applier path.

## 10. Out of scope (rounds 2 and 3)

- **Round 2:** Run on rule cards, Run rules with a rule picker, the rule-run preview with select all, and removing "Run rules on…".
- **Round 3:** removing mail from Gmail permanently in bulk (`batchDelete`) with the full-access permission and reconnecting mailboxes, Delete forever for mail still in Gmail, and the Delete forever rule action.
- Bulk Archive, Mark read, Star or Label on picked mail. Not asked for; rules do these.

## 11. Testing

**Backend:**
- Gmail views exclude offloaded and deleted mail, both in the list and in the counts.
- Archived: a mail with neither INBOX nor SENT shows; a mail with INBOX doesn't; a mail with only SENT doesn't. The thread rule works.
- `in:archived`.
- Migration 0011:
  - local_archive rows become offloaded;
  - the rule action `delete` becomes `offload` in rules, rule versions and decisions;
  - a database from 0.4.0 upgrades.
- YAML import accepts `delete`.
- A rule's Offload ends in OFFLOADED, and restore brings it back.
- Delete forever by ids and by `{view, q}` in both CleanSync views, refusing during a restore.
- An offload start from a pick-all in a label view.
- The old `/api/mail/delete` is gone, and the `archive` view is answered as offloaded.

**Frontend (vitest):**
- The panel's groups and order.
- The filter form: form to box for each field, box back to form (including quoting and unknown words kept), and Clear.
- Select all's three states and the bar text.
- The actions per view.
- The rule form's action list, with Delete forever shown but unavailable.

**Playwright:** build a search with the form, tick Pick all, Offload, find the mail in Offloaded, Restore it. Delete forever in Deleted in Gmail. This updates the existing rules test where it uses Delete and the Local archive.

**Docs:** `docs/mail-backup.md`, `docs/mail-rules.md` (actions, Offloaded, Delete forever) and `docs/limitations.md`. The CHANGELOG entry is written at release, with rounds 2 and 3.
