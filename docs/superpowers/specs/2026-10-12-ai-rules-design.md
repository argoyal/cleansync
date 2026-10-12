# AI rules, model connections and the local archive — design

Date: 2026-10-12. Status: approved in conversation, waiting for review of this written spec.

## 1. What this is for

CleanSync keeps a copy of every Gmail mail. This adds sorting: the owner writes rules in plain language for each mailbox ("newsletters I never read → archive"), and CleanSync sorts mail by them, using a model the owner chooses: a local model (Ollama, LM Studio, llama.cpp, vLLM), a free hosted one, or their own LiteLLM gateway. Ideas are borrowed from inbox-zero (github.com/elie222/inbox-zero), but not its code or its weak points.

This is the first of three specs:

1. **This spec:** model connections, AI rules, and the local archive with Delete forever.
2. Next: bulk unsubscribe (its own spec).
3. Next: draft replies (its own spec).

Later, with their own tickets: the knowledge graph for each mailbox (#17), and removing mail from Gmail immediately with the wider Google permission (#18).

### Success looks like

- New inbox mail is sorted within a few minutes of the backup bringing it in, with no cloud AI service needed.
- The owner trusts a rule by first seeing its suggestions, then switching it to automatic.
- Every change CleanSync makes in Gmail is logged and can be undone.
- The AI is part of the Mail section, not a separate place to visit.

## 2. Fixed rules of this design

- **Everything the model reads comes from the local copy**: the `.eml` file and the database row. Gmail is never read to sort a mail, preview a rule or explain a decision. Gmail is touched only to apply a decision (label changes, trash, untrash).
- A mail that is not backed up yet, or whose copy is unreadable, is not sorted. It waits.
- No mail is ever sorted on a guess. A model that can't be reached, or an answer that can't be read, means "not sorted" or "no rule", never a random action.
- The AI never sends mail and never removes mail from Gmail for good. Its Delete moves mail to Gmail's trash, after checking the local copy.
- Rule actions act in Gmail, and CleanSync's copy follows straight away.
- Rules, remembered senders and decisions belong to one mailbox (account).
- The AI is part of Mail. The only separate place is **Settings → Models**, which is set up once, like the Google connection.

## 3. Shape

One container, one process, SQLite, as today. New parts:

```
backend/app/services/ai/        connections, task → model choice, the OpenAI-style client
backend/app/services/rules/     matching (fixed checks, remembered senders, the AI question),
                                actions, decisions, undo, sentence → rule, rule jobs
backend/app/services/mail/…     the local_archive state, Delete, Delete forever
backend/app/api/rules.py        rules, decisions, rule jobs
backend/app/api/models_ai.py    connections and tasks (Settings → Models)
```

### Flow for new mail

1. The backup's keep-up step (`MailBackup._incremental`) downloads new mail as today.
2. Each newly kept mail that has the `INBOX` label gets a row in `rule_todo` (in the same transaction as the mail row).
3. A **sorting worker** (one asyncio task per mailbox with sorting turned on) takes rows from `rule_todo`, decides, and writes a `rule_decisions` row.
   - The worker runs **outside the mail queue**, so a slow model never holds up backup, offload or restore.
   - Its Gmail calls go through the shared Google client, so they are paced and counted against the same daily budget.
4. In Automatic mode, the decision is applied straight away. In Suggest mode, it waits for the owner.

### Flow for older mail

"Run rules on…" turns a search, a view or a selection into a rule job.

1. **Preview:** decide every mail locally, change nothing, and show counts per rule and action with examples.
2. **Confirm:** the owner types the count.
3. **Run:** the same worker runs the job, with progress on Activity, stop and resume.

A rule job is one decision run. The owner's confirmation counts as approval, so its decisions are applied whatever each rule's mode is; the preview plays the part of Suggest mode. Mails that changed between the preview and the run are decided again, and anything that would now differ is left out and listed.

## 4. Model connections (Settings → Models)

**Connection:** name, base URL (OpenAI-style, for example `http://ollama:11434/v1`, `http://litellm:4000/v1`, `https://openrouter.ai/api/v1`), an optional API key, and optional extra headers.
- The API key is stored as a mode-600 file under `app/data/secrets/ai/<connection-id>`. It is never stored in the database, never returned by the API, and never written to logs (it is added to `core/redact.py`).
- Extra header values are treated as secrets in the same way.

**Task:** what the model is used for. Each task chooses a connection, a model name (free text, plus "List models" from `GET /models`), a temperature (default 0) and a timeout (default 60 s).
- Tasks in this spec: **Sorting**, and **Writing rules** (turning a sentence into a rule). Writing rules uses the Sorting choice unless set.
- Later specs add Drafting, Unsubscribe check and Knowledge graph.

**Client:** `POST {base}/chat/completions` with `httpx`, no SDK. It tries three ways of asking for a structured answer, in order:
1. `response_format: {type: json_schema, …}`
2. `response_format: {type: json_object}` with the shape described in the prompt
3. Plain text, read with a strict parser (`Rule: 3`)

The first that works is remembered for that connection and model (and forgotten when either changes).
- **One retry,** then the call fails with a plain reason.
- Each call records the model, time taken, the answer mode used, and token counts when the server reports them.

**Test button:** sends one fixed sample mail with two sample rules, then shows whether it answered, how long it took, which answer mode worked, and what it chose.

## 5. Rules

### The rule

Stored per mailbox, in table `rules`:

- **Name and description.** The description is the owner's sentence, and is what the AI reads.
- **Fixed conditions** (optional, all must hold):
  - sender is or isn't in a list of addresses or domains
  - subject contains
  - has a `List-Unsubscribe` header
  - is a calendar invite
  - has attachments
  - addressed to me directly vs only in CC, or via a list
- **How the fixed conditions combine with the description:**
  - `fixed_only`: no AI call
  - `fixed_decides`: a fixed match applies the rule; otherwise the AI is asked
  - `fixed_then_ai`: the fixed conditions narrow the candidates, and the AI confirms
  - `ai_only`: no fixed conditions
- **Actions** (one or more): Archive (remove `INBOX`), Add label (the Gmail label is created if missing), Mark read, Star, Delete (section 7).
- **Mode:** `suggest` (the default for new rules) or `automatic`.
- **Position** (order), and **on/off**.

**Versions:** every save writes a row to `rule_versions`. Decisions point to the version that made them. Editing a rule never changes past decisions, and Undo still works.

**Remembered senders** (`rule_senders`): a sender whose mail the owner approved or corrected into the same rule 3 times. They are listed on the rule, and each can be removed.

**Starter rules** (off until switched on, in Suggest mode): Newsletters, Marketing, Receipts, Notifications, Cold outreach, Calendar.

**Export / Import** writes or reads YAML for one mailbox's rules (not decisions). **Copy to another mailbox** copies rules, but not remembered senders.

### Matching one mail

1. **Remembered senders.** On a hit, that rule applies, decided by "remembered sender".
2. **Rules with fixed conditions,** in order:
   - `fixed_only` and `fixed_decides`: the first match wins.
   - `fixed_then_ai`: a match makes the rule a candidate.
3. **AI rules.** The candidates (`ai_only`, `fixed_then_ai` with a match, and `fixed_decides` without one) go to **one AI call**:
   - The rules are listed **by number** with their descriptions.
   - The mail is described by its sender, recipients (and whether the owner is in To or CC), subject, date, attachment names, whether it has `List-Unsubscribe`, and about the first 2,000 characters of plain text. Quoted replies are cut, and links are reduced to their domain.
   - Up to 3 recent corrections for this sender are listed as hints.
   - The mail sits inside clear markers, with an instruction that it is data, not instructions.
   - The answer is `{"rule": N, "why": "…"}`, where `0` means none.
4. **Reading the answer:**
   - A number not in the list counts as `0` and is logged.
   - An unreadable answer counts as `0`, with the raw answer kept (cut to 500 characters).
   - A model that can't be reached leaves the `rule_todo` row in place, and it is retried with a growing wait.

One rule per mail.

### Writing a rule from a sentence or from selected mail

- **From a sentence (the Rules section):** the Writing rules model returns a draft rule (description, any fixed conditions it can see, actions) in a fixed shape. It is shown in the rule form. Nothing is saved until the owner confirms.
- **From selected mail (Make a rule… in the list or reader):**
  - CleanSync fills in what the selected mails share: a sender or domain, a `List-Unsubscribe` header, a calendar invite.
  - The model drafts a one-sentence description from those mails.
  - The owner edits it and saves.
- **Try it,** in the rule form: run the unsaved rule, alongside the existing rules, over selected mails or a search, locally, and show each decision with its "why".

## 6. Decisions, applying and undo

**`rule_decisions`:** one row per mail per decision run. It holds:
- the mailbox, mail, rule and rule version (or none)
- how it was decided: `remembered`, `fixed` or `ai`
- the "why", the model, the time taken
- the planned actions, and the exact label changes (`add`, `remove`, `trash`)
- the status: `suggested`, `applied`, `rejected`, `undone` or `failed` (with a reason)
- the job id, when it came from a rule job

**Applying:**
1. The label changes are recorded on the decision before calling Gmail.
2. Then one `messages.modify` call is made, or `messages.trash` for Delete.
3. The local row (labels, state) is updated at once.

If Gmail refuses, the decision ends `failed` with the reason. When the budget is used up, the worker waits until the budget resets, like offloads do.

**Undo** (one decision or a batch): reverse the recorded changes, or untrash. A change is reversed only while the mail is still in the state CleanSync left it in. Changes the owner made since are never overwritten, and the decision says what it couldn't reverse.

**Corrections** (*wrong rule → X* or *→ none*):
- In Suggest mode, the decision is replaced before anything is applied.
- On an applied decision, it is undone first, then the corrected rule's actions are applied.
- A correction removes the remembered sender behind the mistake, and is kept as a hint for that sender.

**The owner's own moves win:**
- A mail CleanSync archived that shows up in the inbox again on the backup's next check is marked "moved back by you". It is never sorted again, and is listed as a likely correction to confirm.
- A mail the owner moved before a suggestion was approved has its suggestion withdrawn.

**Sorting is idempotent:** a mail gets at most one decision per decision run, and `rule_todo` is keyed by mail. A restart picks up where it stopped.

## 7. Local archive, Delete and Delete forever

**The new state `MailState.LOCAL_ARCHIVE = "local_archive"`:** the mail is in Gmail's trash (or Gmail has since emptied it), and CleanSync keeps the copy.

**Delete** (a rule action, and also by hand from the reader and from a selection):
1. Check the local copy, with the same check offload uses: present, same size, reads as a mail.
2. `messages.trash` in Gmail.
3. Set the state to `local_archive`.

If the check fails, nothing is deleted and the reason is shown.

**Backup interplay** (`index.apply_labels`, `mark_deleted`):
- A `local_archive` mail seen in `TRASH`, or gone from Gmail, stays `local_archive`. It is never switched to "Deleted in Gmail".
- Taken out of the trash in Gmail by the owner, it becomes `in_mail` again, the same as offloaded mail today.

**Hidden from the main mail:** every main Mail view, the counts and Mail search leave out `local_archive`.

**Mail → Local archive** (a view in the Mail panel): its own list and search, using the same search words but limited to archived mail.
- **Restore to Gmail:** the existing restore (untrash, or import when Gmail no longer has the mail). The mail becomes `in_mail` again.
- **Delete forever:** a typed-count confirmation, then removal of the `.eml`, the database row (and the mail's decisions) and its search-index row.
  - A short record (sender, subject, date, when deleted) is written to the Activity log.
  - Cannot be undone; the dialog says so.
  - Only `local_archive` mail can be deleted forever.

**Ticket #18** is trimmed to what is left: an opt-in "remove from Gmail immediately", which needs the wider `https://mail.google.com/` permission.

## 8. Screens

- **Settings → Models:** connections (add, edit, test, remove) and tasks (connection, model, temperature, timeout, Test).
- **Mail list and reader:**
  - a rule chip on decided mail, for example "Newsletters · suggested". Click it to see the "why", then Approve, Correct… or Undo.
  - **Make a rule…** and **Delete** in the selection toolbar and the reader.
  - **Correct rule…** in the reader.
- **Mail panel views:** **Suggested** (waiting decisions grouped by rule, with bulk Approve or Correct) and **Local archive**, next to the existing views.
- **Mail panel → Rules** (for the mailbox in the account switcher):
  - each rule shown as its sentence plus its actions, with mode, on/off, and the number of mails decided in the last 7 days
  - a sentence box at the top to write a new rule
  - drag to reorder
  - Starter rules, Copy to another mailbox, Export / Import, Run rules on…
- **Rule form:** sentence, fixed conditions, how they combine, actions, mode, remembered senders, Try it.
- **Mail status line:** sorting status for each mailbox ("sorted 212 today · 4 waiting · model unreachable since 10:40").
- **Activity:** rule jobs alongside other jobs, and delete-forever records.

## 9. When things go wrong

| What happens | What CleanSync does |
|---|---|
| Model can't be reached, or times out | Mail stays unsorted. Retried with a growing wait. A warning shows after about an hour. |
| Answer can't be read | No rule; the raw answer is kept on the decision |
| Rule number not in the list | No rule; logged |
| Gmail refuses one mail | The decision fails, with the reason |
| Google budget used up | Waits until the budget resets |
| Storage disk missing, or copy unreadable | The mail isn't sorted; Delete refuses |
| No model set up | AI sorting is off. Fixed-only rules and remembered senders still run. |
| Owner moved the mail meanwhile | The suggestion is withdrawn, or Undo skips that change |

## 10. Data (one new migration, `0008_ai_rules.py`)

New tables:

| Table | Holds |
|---|---|
| `ai_connections` | id, name, base URL, has_key, header names, answer modes learned per model |
| `ai_tasks` | task, connection, model, temperature, timeout |
| `rules` | account, name, description, fixed conditions (JSON), how they combine, actions (JSON), mode, position, on/off, current version |
| `rule_versions` | rule, version number, a full copy of the rule, who saved it, when |
| `rule_senders` | rule, sender address, approvals counted, when learned |
| `rule_todo` | account, mail, added at, attempts, next try at, last error |
| `rule_decisions` | the fields listed in section 6 |
| `rule_jobs` | the search, state, counts |
| `rule_corrections` | account, sender, from rule, to rule, when |

`mail_messages` gets a new state value, but no new columns.

## 11. Testing (red, then green, pytest as today)

- **AI client** against a fake OpenAI-style server (`respx`): each of the three answer modes, refusal of json_schema leading to fallback, junk answers, timeouts and the retry, the API key and headers never in logs or API answers.
- **Matching** with a fake chooser (no model): the order, the four ways fixed conditions combine with the description, remembered senders, numbers not in the list, hints from corrections.
- **Applying, undo and corrections** against `devtools/fake_gmail.py`: recorded label changes, partial undo after the owner changed the mail, "moved back by you".
- **Backup interplay:**
  - `local_archive` is never switched to Deleted in Gmail
  - untrashing in Gmail gives `in_mail`
  - mail is added to `rule_todo` only for new inbox mail
- **Search and views** leave out `local_archive`; the Local archive view and its search show only it.
- **Delete forever:** removes the file, the row and the search entry; refuses mail that isn't in `local_archive`; writes the Activity record.
- **Sentence → rule, and Make a rule…** with a fake model: nothing is saved until confirmed.
- **Browser test (Playwright):**
  1. Set up a fake model and create a rule from a sentence.
  2. The new mail gets a suggestion; approving it moves the mail out of the inbox.
  3. Undo it.
  4. Delete a mail, then Delete forever from the Local archive.

## 12. Not in this spec

Bulk unsubscribe, draft replies, the knowledge graph (#17), removing mail from Gmail immediately (#18), embeddings, more than one rule per mail, and Gmail filters.
