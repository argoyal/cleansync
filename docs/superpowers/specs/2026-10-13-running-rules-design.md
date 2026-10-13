# Running rules: Run on a rule, Run rules on a search (0.5.0, round 2 of 3)

Status: draft for review · 2026-10-13 · builds on [mail panel and search](2026-10-13-mail-panel-and-search-design.md) and [AI rules](2026-10-12-ai-rules-design.md)

## 1. Why

Today **Run rules on…** sits on the Rules page and above most mail lists. On the Rules page it silently means "the whole inbox". No mail is shown there, so it's unclear what it will do. It always runs every rule, and approval means typing a count against a summary, with no list of the mails.

The owner wants rules to work like this:
- **Write rules in plain words.** Run a rule by hand over the inbox, see the list of mails it matched, approve, and only then does its action happen.
- **Run chosen rules on a search** when they know a rule fits that search.
- **Keep "Sort new inbox mail" as it is,** for new mail: Automatic rules act, Suggest-first rules land in Suggested.

## 2. What success looks like

- Each rule card on the Rules page has a **Run** button. It decides that one rule over the whole Inbox, shows the matching mails, and acts on the ones you approve.
- Above a search result in a Gmail view, a **Run rules** button opens a list of the mailbox's rules. You tick the ones to run on those mails, then see the matches and approve.
- **Run rules is only offered when there is a search, or a pick** (by hand or Pick all). A plain view with no search (Inbox, All mail, a label) doesn't offer it. The Rules page doesn't offer it at all.
- The match list has **Pick all** (round 1's three-state checkbox). It starts **ticked**, so approving the whole list is one click. You can untick mails you want left alone.
- "Run rules on…" no longer exists anywhere.

## 3. Run on a rule card

- The **Run** button sits on every enabled rule's card, next to Edit. It is disabled, with a short reason, when:
  - the rule is turned off ("Turn the rule on to run it."), or
  - the rule needs the model and no Sorting model is set ("Set up a model in Settings → Models.").
  
  A rule with only fixed conditions runs without a model.
- **It covers the Inbox:** mail in Gmail with the `INBOX` label. The Marketing example: Run → every Inbox mail the rule fits → approve → those mails are archived.
- **It looks only at this rule.** Each Inbox mail is decided against this rule alone (remembered senders for this rule, its fixed conditions, then the model with this rule as the only choice). Other rules, and their order, play no part.
- **Mail already being decided by sorting, and mail you moved back yourself, are skipped,** as runs do today.
- **For more than the Inbox,** use a search in All mail (or Archived, or a label), then **Run rules** and tick that rule.

## 4. Run rules on a search or a pick

- **Where it shows:** the button appears in the Gmail views (Inbox, Starred, Important, Sent, Archived, All mail, labels):
  - when the search box isn't empty: it then runs on every mail of the search in that view;
  - or when mails are picked (by hand or Pick all): it then runs on the pick.
  
  It doesn't show in Suggested, Offloaded or Deleted in Gmail.
- **The rule picker** opens first:
  - It lists the mailbox's enabled rules in their order, each with a checkbox, its name and its actions.
  - At the top it shows "Run on: 1,284 mails matching `from:shop.example.com`" (or "on 37 picked mails").
  - Nothing is ticked at first. **Run** is enabled once at least one rule is ticked.
- **Several rules:** each mail gets at most one rule, the first ticked rule that fits, in the rules' order. This is the same as sorting, limited to the ticked rules.

## 5. Deciding, then the match list

Both ways start a run (today's rule job) with the set of rules to use. Then:

**1. Deciding.** CleanSync decides each mail using only its own copy, exactly as today. The dialog shows "Deciding… 340 of 1,284" with a bar, and the model-pause line from 0.4.0 when the model is paused. You can close the dialog. The run keeps deciding, and the Rules page shows "A run of rules is waiting for you" with **Open**, as today.

**2. The match list.** When deciding is done, the dialog shows:
- **The header:** "Marketing would archive 412 mails. 872 didn't fit." With several rules, it gets one line per rule ("Marketing · archive · 412", "Receipts · label Receipts · 30").
- **The list of the matched mails,** paged (50 at a time, with "Show more"). Each row has a checkbox, the sender, the subject, the date and the rule's short reason (the model's "why", or "Its fixed conditions match"). With several rules, each row also carries the rule chip, and a rule filter above the list shows one rule's matches.
- **Pick all** at the top of the list, **ticked when the list opens.** Unticking one row keeps all the others picked: the server stores the mails you left out, not the ones you kept.
- **The approve button names the action and the count,** for example **Archive 412 mails**, or **Apply 442 changes** with several rules or mixed actions. Unticked mails are left alone and recorded as "left out by you".
- **The typed count stays for Offload and Delete forever only.** If any picked change offloads or deletes forever, you type the number of those mails before the button works: "Type 120 to offload these mails". Archive, Label, Mark read and Star need only the button.

**3. Making the changes.** As today: one by one in Gmail, you can close the dialog, Stop stops it, and a mail that changed since it was decided is skipped. The end shows applied, skipped (with the reasons), failed and left out.

**When nothing matches:** "No mail fits this rule." with Close.

## 6. What stays the same

- **Sort new inbox mail**, the Suggested view and its Approve, Approve all and Correct…, Undo on applied changes, and remembered senders.
- **One run at a time per mailbox,** and at most 20,000 mails per run ("That's more than 20,000 mails. Please narrow the search.").
- **The model rules from 0.4.0:** a paused model leaves a run deciding until it is back.

## 7. Removed

- **Run rules on…** on the Rules page and in the mail header.
- **The approval summary that needed the typed total of every change.** It is replaced by the match list, with the typed count kept only for Offload and Delete forever.

## 8. Stored state and API (for the plan)

**Database (migration 0012):**
- `rule_jobs.rule_ids`: JSON list of rule ids, not null. It is set on every new run. Rows from before the upgrade get `[]`, which means "all enabled rules".
- `rule_jobs.view` and `rule_jobs.q` are saved so the header can say what ran.

**Starting a run.** `POST /api/rule-jobs` takes:
- `account_id`;
- `rule_ids` (at least one);
- and **one of:**
  - `{view, label, q}` with a non-empty `q`;
  - `{view, label, q, pick_all: true}`;
  - `message_ids` (a pick by hand);
  - `inbox: true` (the rule-card Run, which needs exactly one rule id).
  
A start without a search and without a pick is refused: "Search first, or pick some mail."

**Deciding** uses only the run's rules:
- `rule_views` is filtered to `rule_ids`, and so are the remembered senders;
- correction hints are unchanged.

**The match list.** `GET /api/rule-jobs/{id}/matches?rule_id=&before_id=&limit=50` returns the PLANNED decisions with the mail's sender, subject and date, the rule, the actions and the why.

**Approving.** `POST /api/rule-jobs/{id}/run` takes:
- `left_out: [decision ids]`, which may be empty;
- `confirm: <number>`, required only when picked changes offload or delete forever, and equal to that count.

Left-out decisions become `rejected`, with the reason "You left it out." The rest run as today.

**Preview counts.** `JobPreview` adds per-rule counts (already there), `view`, `q` and `rule_ids`.

## 9. Out of scope

- Changing what Sort new inbox mail does.
- Running rules in the CleanSync views (Offloaded, Deleted in Gmail).
- Scheduling a rule to run by itself on older mail.
- The Delete forever action working (round 3). Until then a rule can't have it, so no run offers it.

## 10. Testing

**Backend:**
- A run with `rule_ids=[r]` decides only with `r`. A mail that another rule would take but `r` fits too gets `r`. A mail only another rule fits gets no rule.
- Remembered senders are limited to the run's rules.
- `inbox: true` covers only `INBOX` mail, and needs one rule.
- A start with no search and no pick is refused.
- The match list pages, and filters by rule.
- Approving with `left_out` turns those into rejected and runs the rest.
- `confirm` is required and checked only when Offload is involved.
- Migration 0012, including old jobs.

**Frontend (vitest):**
- A rule card's Run, its disabled states and their reasons.
- Run rules shows only with a search or a pick, and not in CleanSync views.
- The rule picker needs a ticked rule.
- The match list: Pick all starts ticked, unticking keeps the others, and the button text with one rule and with several.
- The typed count shows only for Offload.

**Playwright:**
- Starter rule Marketing → Run on its card → the list shows the fake marketing mails → approve → they're archived and appear in **Archived**.
- A search → Run rules → tick one rule → untick one mail → approve → that mail is untouched.

This replaces the parts of the existing rules browser test that use "Run rules on…".

**Docs:** `docs/mail-rules.md` §4 is rewritten as "Running rules by hand".
