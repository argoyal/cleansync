# Model limits: pause a connection until its limit resets

Status: draft for review · 2026-10-13 · builds on [AI rules](2026-10-12-ai-rules-design.md)

## 1. Why

The owner wants to sort mail with free AI endpoints (Groq, OpenRouter `:free`, Gemini, NVIDIA and so on). Free tiers come with limits, most of them daily. Today CleanSync handles a model error one mail at a time. Each failed mail waits 2 to 30 minutes, but the worker moves on to the next mail in the queue 15 seconds later. So once a daily limit is used up, CleanSync keeps calling the provider all day: one failing call per mail every 30 minutes. A limit reply also costs extra calls. A 429 is retried after 2 seconds, and a quota error sent as 400 or 422 makes CleanSync try all three answer formats. The provider's own explanation is thrown away, and the status line only warns after an hour.

What the owner asked for: **as soon as a provider says its limit is hit, CleanSync stops calling it. Everything still to be sorted stays in the queue, and is tried again when the limit resets, usually the next day.**

## 2. What success looks like

- After a limit reply, CleanSync makes no more calls to that connection until the reset time. That covers every mailbox, every task, Run rules previews, Try it and Write the rule.
- No mail is lost, guessed, or charged a try because of a pause. When the limit resets, the queue drains by itself.
- The pause survives a container restart. It is kept only in the database, never only in memory.
- The owner sees at once that sorting is paused, until when, why, and the provider's own message. They can override it with **Resume now**.

## 3. Decisions made with the owner

| Question | Decision |
|---|---|
| What pauses | **The connection** (one provider address plus key). Free quotas are counted per key, so every task and mailbox using that connection pauses together. |
| Backup models | **No.** While a connection is paused, its work waits. |
| When "the next day" starts | **(A)** The time the provider gives (`Retry-After` or a rate-limit reset header). Without one, use the connection's **Daily limit resets at** time, which defaults to 00:00 UTC and can be changed, for example to midnight Pacific for Gemini. |
| Where the pause lives | **The database only.** Every pause and its reason are written to the database before CleanSync acts on them. |
| Request budgets per minute or day | Out of scope. The pause covers the case. |

## 4. Three kinds of failure

The AI client puts every failed model call into one of three kinds. Section 5 says how long each kind pauses the connection.

| Kind | What it is | Examples |
|---|---|---|
| **limited** | The provider says a limit or quota is used up. | HTTP 429. HTTP 402. Any other 4xx whose message contains one of: `rate limit`, `rate_limit`, `ratelimit`, `quota`, `limit exceeded`, `limit reached`, `too many requests`, `credits`, `exhausted`. The match ignores case. |
| **down** | The server can't be reached or is failing. | Connection refused, DNS failure, timeout, HTTP 5xx, a reply that isn't JSON. |
| **needs_you** | The settings are wrong and waiting won't fix it. | 401 and 403 without limit wording ("refused the API key"), 404 ("no model called …, or the address is wrong"), any other 4xx without limit wording, an address that isn't valid. |

A 400, 404, 405, 415 or 422 reply to a request that asked for a JSON answer format still means "try the next format", as it does today. The exception is a reply with limit wording: that is **limited** at once, and no other format is tried.

A **limited** reply is never retried. A **down** reply keeps today's single retry after 2 seconds, and only then counts as **down**.

## 5. How long a pause lasts

The pause end is worked out once, when the failure happens, and saved.

**limited:**
1. If the reply has `Retry-After` (seconds or an HTTP date), or one of `x-ratelimit-reset-requests`, `x-ratelimit-reset-tokens` or `x-ratelimit-reset` (as seconds, a duration like `6m0s`/`1h2m3.5s`, or a Unix time), the pause ends at that time. If several are given, the latest one wins. Times more than 7 days away are capped at 7 days.
2. Otherwise the pause ends at the next **Daily limit resets at** time for the connection, which is always in the future. If that is less than 1 minute away, the following day's reset time is used.
3. A wait the provider gave that is shorter than 2 minutes is a per-minute limit. The connection is paused for that long (at least 5 seconds), not for a day.

**down:** 1 minute, then 5, 15, 60, then 360 minutes for every later one. The step is the number of **down** pauses in a row and is saved on the connection. The first call that works resets it to 0.

**needs_you:** no end time. The pause stays until the owner changes the connection (address, key or headers), changes a task that uses it, presses **Resume now**, or runs a **Test** that works.

## 6. Stored state (migration 0010)

New columns on `ai_connections`:

| Column | Type | Meaning |
|---|---|---|
| `resets_at` | text `HH:MM`, default `00:00` | Local time the daily limit resets. |
| `resets_tz` | text, default `UTC` | IANA time zone for `resets_at`, for example `America/Los_Angeles`. Checked against `zoneinfo` when saved. |
| `paused_until` | datetime (UTC), null | When the pause ends. Null with `pause_kind` set means **needs_you**. |
| `pause_kind` | text, null | `limited`, `down` or `needs_you`. Null means not paused. |
| `pause_reason` | text, null | CleanSync's plain sentence, for example "The daily limit is used up." |
| `pause_detail` | text, null | The provider's own message, cut to 500 characters, with key-like text removed (see section 9). |
| `paused_at` | datetime (UTC), null | When the pause began. |
| `down_streak` | integer, default 0 | Number of **down** pauses in a row. |

The `rule_todo` queue and the remembered answer formats are already in the database and need no change.

**Restart rule:** no pause information is held only in memory. After a restart, the first model call reads the connection row and refuses if a pause is still running. A pause whose `paused_until` has passed counts as over.

## 7. The gate in the AI client

Every model call (`ask` and `list_models`) goes through `AiClient`, which already knows the connection id from `AiTarget`.

1. **Before calling:** read the connection's pause from the database. If it is paused (`pause_kind` set and either `paused_until` is null or still in the future), raise `AiPaused(kind, until, reason, detail)` without any network call. `AiPaused` is a new kind of `AiError`.
2. **After a failure:** work out the kind (section 4) and the end (section 5). Write the pause to the connection row and commit, then raise `AiPaused`. Two failures racing for the same connection both write a pause. The row ends up with the later end, which is fine.
3. **After an answer that works:** if the connection has a pause, or `down_streak` is above 0, clear it in one update.

`list_models` follows the same rules, but a **needs_you** failure from it does not pause the connection: the owner is in the middle of editing settings. **Test** ignores a running pause, makes the call, and clears the pause if the call works. That is how "press Test to check" works.

The old `AiUnreachable` and `AiRefused` stay as the way a single call reports its failure inside the client. Callers outside the client only see `AiPaused`, except Test, which gets the failure directly so it can show it.

## 8. Sorting, runs and the other callers

**Sorting worker** (`rules/worker.py`):
- `AiPaused` while deciding a mail: the mail's to-do row is **not** changed. No try is counted, `next_try_at` stays the same, nothing is written to `last_error`. The batch stops.
- While the Sorting connection is paused, the worker makes no model calls. It still decides to-do mails that don't need the model, as described below.
- The loop sleeps until the earliest of: the next due to-do row of a mailbox that isn't paused, the earliest `paused_until`, or the usual 15-second idle time. A **needs_you** pause only wakes on a poke, from saving settings or from **Resume now**.
- The old "failing for an hour" warning (`failing_since`, `last_error`) stays for errors that aren't model pauses, such as a mail copy that can't be read. A model pause no longer sets it.

**Rules that need no model.** `decide()` already tries remembered senders and fixed-condition rules before asking the model. When the connection is paused and the mail's result needs the model, `AiPaused` makes the mail wait. When it doesn't (a remembered sender, or a rule set to "Only the fixed conditions" that matches), the mail is decided as usual. To let this happen while paused, a paused mailbox is not skipped wholesale. Each to-do mail is offered to `decide()`, and the batch stops at the first `AiPaused`. To avoid spinning on the same waiting mails, a mail that stopped on `AiPaused` gets `next_try_at` set to the pause end (or one hour later for **needs_you**). Its try count does not change. **Resume now** and a working Test set `next_try_at` back to now for every to-do row of that connection's mailboxes.

**Run rules preview** (`rules/jobs.py`): uses the same to-do rows, so it waits the same way. While the job's connection is paused, the job shows the pause reason, and the dialog shows it below the progress bar. Starting a new run while the Sorting connection is paused is allowed. The preview simply waits.

**Write the rule, Make a rule…, Try it:** on `AiPaused`, return the same answer as "no model set" (Write the rule and Make a rule… open the form with the sentence), plus the pause line. Try it says "The model is paused until 05:30: <reason>" and changes nothing.

## 9. Showing it

**API:**
- `GET /api/ai/connections` adds `resets_at`, `resets_tz` and a `pause` object (`kind`, `until`, `reason`, `detail`, `since`) or null.
- `POST /api/ai/connections/{id}/resume` clears the pause and `down_streak`, sets `next_try_at = now` for waiting to-do rows (see section 8), and pokes the worker.
- `PATCH /api/ai/connections/{id}` accepts `resets_at` and `resets_tz`. Changing the address, key or headers clears a pause. So does saving a task that uses the connection.
- The sorting status (`GET /api/rules/sorting`) adds `paused` with the same fields, for the Sorting connection.
- A rule job adds `paused` the same way.

**Removing key-like text from `pause_detail`:**
- Strings that look like keys are replaced by `[removed]`. This covers `sk-…`, `nvapi-…`, `gsk_…`, `AIza…`, `Bearer …`, and any run of 24 or more letters, digits, `_` or `-`.
- The connection's own saved key and header values are also replaced, using the redactor the logs already use.
- The detail is taken from the JSON `error.message` or `message` field if there is one, otherwise from the start of the reply body.

**UI:**
- **Settings → Models, each connection:**
  - a **Daily limit resets at** time field and a time-zone picker (a short list of common zones plus free text);
  - a paused connection shows a badge with the kind and end time, for example "Paused until 05:30 — daily limit used up", the provider's message (shown on click) and **Resume now**.
- **Mail status line:** "Sorting paused until 05:30 (Groq: daily limit used up). 412 mails waiting." It shows as soon as the pause starts. For **needs_you**: "Sorting stopped: Groq refused the API key. Check Settings → Models."
- **Run rules dialog:** the same line below "Deciding… x of y".
- **Test result:** shows the provider's message (with keys removed) as well as CleanSync's sentence.

## 10. Small fixes carried along

- A connection address ending in `/chat/completions` has that removed when saved, so `…/v1/chat/completions` becomes `…/v1`.
- A 405 from the model server gets this message: "The server doesn't accept chat requests at this address. It usually ends in /v1."

## 11. Out of scope

- Backup models for a task.
- Request budgets per minute, per day, or by tokens.
- Counting or showing tokens used.
- Pausing one model on a connection while others keep going. The whole connection pauses.

## 12. Testing

**AI client (pytest + respx):**
- 429 with `Retry-After: 120`, with an HTTP-date `Retry-After`, and with `x-ratelimit-reset-requests: 6m0s`;
- 429 with no headers, which pauses until the daily reset;
- 402;
- 400 with "quota exceeded", which is limited and no other format is tried;
- 403 with "rate limit", which is limited;
- 403 without limit wording, which is needs_you;
- 404, which is needs_you;
- 503 twice, which is down with step 1;
- a timeout, which is down;
- `Retry-After: 20`, which is a 20-second pause;
- a provider time 30 days away, which is capped at 7 days;
- a 429 is never retried.

**Reset time:**
- 00:00 UTC, from just before and just after midnight;
- `America/Los_Angeles` across a daylight-saving change;
- a reset less than 1 minute away moves to the next day.

**Gate:**
- a paused connection makes no HTTP call;
- an expired pause lets the call through;
- a call that works clears the pause and `down_streak`;
- Test bypasses the pause and clears it on success;
- a **needs_you** from `list_models` doesn't pause.

**Restart:** write a pause to the database, build a fresh `AiClient` and worker on it, and run one pass. It makes no HTTP calls, and try counts don't change.

**Worker:**
- while paused, try counts don't change and `next_try_at` moves to the pause end;
- a remembered sender and a fixed-conditions-only rule are still decided while paused;
- after the pause end, the queue drains;
- the loop sleeps until the pause end;
- **Resume now** wakes it and makes waiting mail due.

**API:**
- resume;
- reset fields, including a bad time zone being refused;
- changing the address clears the pause;
- the pause shows in the sorting status and the job status;
- key-like text is removed from `pause_detail`.

**Frontend (vitest):** the paused badge and Resume now, the status line text for each kind, the run dialog line, and the reset-time fields.

**Playwright:** the fake model server gets a "limited" switch that returns 429 with `Retry-After`. Test shows the provider's message. Sorting a mail shows the paused status line. **Resume now** with the switch off clears it.

**Docs:** `docs/mail-rules.md` §1 and §7, `docs/limitations.md`, and the CHANGELOG.
