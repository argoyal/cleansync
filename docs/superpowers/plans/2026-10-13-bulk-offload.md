# Offload in Bulk and Delete Forever in Gmail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An offload removes mail from Gmail one batch of up to 1,000 at a time (for good when the mailbox allows permanent delete, else to Gmail's trash), and Delete forever works on mail still in Gmail, by hand and as a rule action, for mailboxes that opted in.

**Architecture:**
- The Gmail adapter gets `batch_delete`, `batch_trash` and `delete`. A missing full permission is its own error (`PermanentDeleteRefused`), never "reconnect mail".
- Each mailbox has a switch (`accounts.permanent_delete`, migration 0013). It counts only while Google granted `https://mail.google.com/` (kept in `accounts.scopes`). Turn on asks Google for that one extra scope; losing it at Google turns the switch off with a note.
- The offload job checks every copy of a batch, then makes one Gmail call for the batch. A refused batch falls back to one mail at a time. The same job machinery runs Delete forever on mail in Gmail (`mail_offloads.kind = "delete_forever"`). The rule applier is still the one path for a rule's Offload, Offload on one mail and the new Delete forever rule action.
- The frontend gets the switch in Settings → Accounts, the space texts, Stop, Delete forever in the Gmail views and the reader, and the rule form's Delete forever.

**Tech Stack:** FastAPI, SQLAlchemy 2 + Alembic on SQLite, httpx + respx, pytest (`asyncio_mode = "auto"`), React 18 + TanStack Query + Tailwind, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-13-bulk-offload-design.md` (approved; round 3 of 3 of 0.5.0; closes issue #18).

**Runs after** `docs/superpowers/plans/2026-10-13-mail-panel-and-search.md` (round 1, migration 0011) and `docs/superpowers/plans/2026-10-13-running-rules.md` (round 2, migration 0012), on the same branch. This plan names round 1's code exactly as its plan writes it (`Pick(view=, label=)`, `describe_pick`, `candidates`, `forever.py`, `POST /api/mail/delete-forever`, `store.NOT_YET_KINDS`, `Applier.offload_by_hand`, the rule action kinds, `DeleteForeverDialog`, `MessageMailActions`, the picked-mail bar in `MailReader`). From round 2 it uses `jobs.HEAVY_KINDS` (the run's typed count already covers `delete_forever`) and touches its code in one place, which Task 8 names: `_withdraw_suggestions` in `backend/app/services/rules/jobs.py`.

## Global Constraints

- **Words in the app:** the switch is **Allow permanent delete**. Exact copy:
  - Off: "Offload moves mail to Gmail's trash. Turn this on to delete it from Gmail directly, so the space comes back at once." with **Turn on**.
  - On: "Offload and Delete forever remove mail from Gmail directly. Gmail's trash isn't used." with **Turn off**.
  - Lost: "Permanent delete was turned off: Google no longer allows it for this mailbox."
  - Not allowed (disabled buttons, waiting rule decisions, the rule card, the server's refusals): "Turn on Allow permanent delete in Settings → Accounts."
  - Space: "Removed from Gmail. The space is free now." in place of "In Gmail's trash until …".
- **The scope:** `https://mail.google.com/` (`FULL_MAIL_SCOPE`). CleanSync keeps asking for `gmail.modify`. Only **Turn on** asks for the full scope; every other sign-in asks for what it asks for today (`include_granted_scopes=true` keeps a scope Google already granted).
- **On means both:** the switch is on **and** `FULL_MAIL_SCOPE` is in `accounts.scopes`.
- **Batches:** at most 1,000 mail ids per Gmail call (`GMAIL_BATCH = 1000`).
- **Quota:** `gmail.messages.batchDelete` 50 units, `gmail.messages.batchModify` 50 units, `gmail.messages.delete` 10 units, counted in the existing daily budget.
- **Safety step:** no mail leaves Gmail before its copy here passed today's check (the file can be read, and its size and SHA-256 match). CleanSync never removes its own copy of mail that is still in Gmail.
- **Delete forever rules:** always Suggest first (the form disables Automatic, the server refuses it), and stand alone like Offload. No Undo.
- **Migration:** `0013`, `down_revision = "0012"`.
- **Not in this round:** emptying Gmail's trash for older offloads, revoking the permission at Google from CleanSync, permanent delete for Drive files, the version bump and the CHANGELOG (the release does those).
- **Copy style:** plain everyday words, matching the existing UI text.
- **Tests must pass:** backend `cd backend && uv run pytest -q`, lint `uv run ruff check . && uv run ruff format --check .`. Frontend `cd frontend && npm test -- --run && npm run lint && npm run typecheck`.
- **Commits:** every commit message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Plan decisions on the spec

These settle details the spec left open. Reviewers judge against them.

1. **Where the granted scopes live.** The spec says `granted_scopes`. That is the function; the column is `accounts.scopes`. The switch is `accounts.permanent_delete` (boolean), and its note is `accounts.permanent_delete_note` (text, the "Lost" sentence, cleared by Turn on or Turn off).
2. **Turn on and off** is `POST /api/accounts/{id}/permanent-delete` with `{"on": bool}`, which answers `{"auth_url": str | null}`. It needs a connected account with Mail set up ("Set up mail backup for this account first."). When Google already granted the scope, the switch goes on without a sign-in. A pretend local account (development and CI) gets the scope without Google, the way Mail setup does. After Google's sign-in the browser lands on `/?connected=<id>&permanent=on` or `&permanent=denied`.
3. **Losing the scope** is noticed two ways: (a) a sign-in whose granted scopes lack it; (b) Gmail answering 403 "insufficient permission" to `batchDelete` or `messages.delete`. Either turns the switch off, saves the note, drops the scope from `accounts.scopes` (so the next Turn on asks Google), and writes one Activity warning. Mail never goes to "Reconnect mail" over this scope alone. When the whole Gmail permission is lost (`mail_needs_reconnect` with `permission_gone`), the full scope is dropped too.
4. **The batch size is fixed at 1,000**, separate from the backup's `mail_batch_size` setting. Progress, Stop and the disk stop are checked before every batch.
5. **Temporary errors.** Rate limits (429, and 403 rate-limit answers) are already waited out by `GoogleClient`; if they last, the offload waits for the next minute, as today. Gmail server errors and network trouble (`MailUnreachable`) on a batch call are tried twice more, after 5 and 20 seconds. If they last, the offload fails as today ("CleanSync couldn't reach Gmail …").
6. **The one-at-a-time fallback** (after any other refusal of a batch) calls `messages.delete` (permanent) or `messages.trash`. There is no per-mail check after the call: Gmail's answer is the check, as for the batch. "Not found" counts as offloaded and as removed from Gmail. Five refusals in a row still stop the run.
7. **Mail that left Gmail before its batch was recorded.** A pending mail whose row a check marked "Deleted in Gmail" is sent again after its copy is checked. Gmail ignores a removal of mail it no longer has. This is today's "claim on resume" rule, now per batch.
8. **A restart resumes.** Today a restart ends a running offload as failed. With batches of 1,000 that would leave up to 1,000 mails gone from Gmail but shown as "Deleted in Gmail". So `recover_mail_offloads` now puts a running offload back to waiting: "CleanSync restarted during this offload. It goes on by itself." Its mail job ends `INTERRUPTED` with the same words, and the minute tick starts the offload again. An offload the owner asked to stop ends stopped instead.
9. **Stop.** Offloads had no Stop button; the spec asks for one. `POST /api/mail/offloads/{id}/stop` ends a waiting offload at once. A running one stops before its next batch. It ends `FAILED`, which the app already shows as "Stopped", with "Stopped by you. What wasn't offloaded yet stayed in Gmail." A Delete forever job says "What wasn't deleted yet stays in Gmail, with CleanSync's copy." instead.
10. **Space counts.** `mail_offloads.removed` counts mails removed from Gmail for good. An offload's API answer gains `kind`, `removed` and `trashed` (`done - removed`). The page shows "Removed from Gmail. The space is free now." when nothing was trashed, today's trash line when nothing was removed, and both lines with counts when mixed.
11. **A rule's Offload, and Offload on one mail,** call `messages.delete` when permanent delete is on and `messages.trash` otherwise. A permanent one records `"removed": true` in the decision's changes. Undo can't bring it back, so the chip offers no Undo (`undoable: false`) and the undo endpoint leaves it with "It was removed from Gmail for good, so Undo can't bring it back. Use Restore to Gmail in Offloaded." Restore to Gmail imports it again.
12. **Delete forever on mail in Gmail** goes through `POST /api/mail/delete-forever` (round 1's endpoint). A Gmail view (`all`, `inbox`, `sent`, `starred`, `important`, `archived`, a label) starts a `mail_offloads` row of kind `delete_forever`. It runs through the queue action `"offload"`, so it waits behind other mail jobs of the mailbox like an offload does. Its mail job kind is `DELETE_FOREVER`. The answer is `{"deleted": 0, "left": n, "job_id": id}`; Offloaded and Deleted in Gmail answer as in round 1, with `"job_id": null`. These jobs don't show in the Batches tab or in Recovery.
13. **Delete forever has no minimum age** (like Offload on one mail in round 1). It refuses a pick-all of All mail with no search and no label (round 1's "Choose at least one filter, or pick mails in the list."). It also refuses while the backup is still listing the mailbox, as an offload does. It needs no restore check: restore and this job both run in the mailbox's mail queue, so they never run side by side.
14. **Activity for Delete forever in Gmail views:** one record per batch, "Deleted 412 mails forever from Gmail", keeping up to 500 senders, subjects and dates plus how many more.
15. **The Delete forever rule action is applied by the applier, one mail at a time**: `messages.delete`, then CleanSync's copy. It runs inside the approval that already exists (Suggested, or a run's approval, which has progress and Stop), not as a separate job. The decision goes with the mail (its rule history), so Activity keeps the record. Records are grouped per rule: a decision applied within 10 minutes of the rule's last record adds to it ("Rule Junk deleted 37 mails forever").
16. **Suggested asks for the typed count too.** Approving Delete forever suggestions (Approve, Approve all, or the chip's Approve) needs the count of those mails typed: "Type 3 to delete these mails forever." The server refuses without it. Other suggestions keep one-click approval. The spec asks for the count only on runs; deleting forever with one click would be the only one-click step that can't be undone.
17. **Saving a Delete forever rule** needs permanent delete on, but only when the save adds the action. A rule that already has it can still be edited or turned off after the permission is gone. A YAML import goes through the same check. Drafting from a sentence doesn't offer the action to the model.
18. **When permanent delete is off,** a suggested Delete forever decision stays suggested with the reason "Turn on Allow permanent delete in Settings → Accounts.". A planned one in a run ends failed with that reason, so the run never loops on it. The rule card shows the same sentence (`blocked` on the rule).
19. **Restore after a permanent delete** needs no new code. The restore summary's word changes from "imported" to "imported again": "Put 3 mails back in Gmail (3 imported again)".
20. **The fake Gmail's answers.** The fake answers `batchModify` with 400 when any id is unknown, `batchDelete` quietly, and `DELETE /messages/{id}` with 204 (404 when unknown). Tests use that 400 to reach the fallback.

## Review Focus

1. **Google takes the full permission back between Turn on and an offload.** The offload must still finish (to the trash), Mail must stay working, and the switch must show the note. Owned by Task 4 (`test_when_google_refuses_the_full_permission_the_batch_goes_to_the_trash`) and Task 8 (`test_google_refusing_it_turns_the_switch_off_and_keeps_the_mail`).
2. **A batch Gmail did but CleanSync never recorded** (a pause or a restart right after the call). It must be sent again and counted once, in both modes. Owned by Task 4 (`test_a_batch_gmail_did_but_cleansync_never_recorded_is_sent_again_and_counted_once`) and Task 5 (`test_a_restart_puts_a_running_offload_back_to_waiting`).
3. **One bad id among the batch, or a mail Gmail no longer has.** The rest must go, the bad one must stay in Gmail with its reason, and a missing one must count as removed. Owned by Task 4 (`test_a_refused_batch_goes_one_at_a_time_and_only_the_bad_mail_stays`, `test_in_the_fallback_a_mail_gmail_no_longer_has_counts_as_offloaded`).
4. **Delete forever on a mail Gmail refuses to delete.** Its copy must stay and it must be listed as not deleted. Owned by Task 7 (`test_a_mail_gmail_wont_delete_keeps_its_copy`).
5. **Approving Delete forever suggestions with one click.** It must be refused without the typed count. Owned by Task 8 (`test_approving_it_in_suggested_needs_the_typed_count`) and Task 12 (`asks for the count before approving Delete forever suggestions`).

## File Structure

Backend:
- `backend/app/services/mail/adapter.py`: `PermanentDeleteRefused`; the protocol's `batch_delete`, `batch_trash`, `delete`.
- `backend/app/services/mail/gmail.py`: the three calls (`BATCH_MAX = 1000`).
- `backend/app/services/google/quota.py`: their costs.
- `backend/app/devtools/fake_gmail.py`: the fake's `batchDelete`, `batchModify`, `DELETE`.
- `backend/app/database/migrations/versions/0013_permanent_delete.py`: the new columns.
- `backend/app/models/accounts.py`, `backend/app/models/mail.py`, `backend/app/models/enums.py`: the columns, `MailOffloadKind`, `MailJobKind.DELETE_FOREVER`.
- `backend/app/services/accounts/oauth.py`: `FULL_MAIL_SCOPE`, `PERMANENT_PURPOSE`, `build_auth_url(scope=)`.
- Create `backend/app/services/accounts/permanent.py`: whether it's on, turning it off, losing it.
- `backend/app/services/accounts/service.py`, `services.py`, `backend/app/api/accounts.py`, `backend/app/schemas/accounts.py`: the switch.
- `backend/app/services/mail/offload.py`, `offload_plan.py`, `backend/app/api/mail_offload.py`, `backend/app/schemas/mail.py`: batches, Stop, restart, Delete forever jobs.
- `backend/app/services/mail/forever.py`, `backend/app/api/mail_forever.py`: shared copy removal, Gmail views.
- `backend/app/services/rules/apply.py`, `store.py`, `decisions.py`, `jobs.py`, `backend/app/services/mail/listing.py`, `backend/app/api/rules.py`, `backend/app/schemas/rules.py`, `backend/app/main.py`: the applier and the rule action.

Frontend:
- `frontend/src/api/types.ts`, `frontend/src/api/rules.ts`, `frontend/src/lib/mail.ts`, `frontend/src/hooks/useMailOffload.ts`, `frontend/src/hooks/useRules.ts`.
- Create `frontend/src/components/settings/PermanentDelete.tsx`; modify `frontend/src/pages/SettingsPage.tsx`, `frontend/src/routes.tsx`.
- `frontend/src/pages/MailOffloadPage.tsx`, `frontend/src/components/mail/OffloadMailDialog.tsx`, `MailRecovery.tsx`, `RuleChip.tsx`.
- `frontend/src/components/mail/DeleteForeverDialog.tsx` (`ForeverButton`, the job progress), `MailReader.tsx`, `MailActions.tsx`.
- Create `frontend/src/components/mail/ApproveForeverDialog.tsx`; modify `frontend/src/components/rules/RuleForm.tsx`, `RuleList.tsx`.
- Create `frontend/e2e/permanent-delete.spec.ts`; modify `frontend/e2e/helpers.ts`, `frontend/playwright.config.ts`.

Docs: `docs/google-oauth-setup.md`, `docs/mail-backup.md`, `docs/mail-rules.md`, `docs/security.md`, `docs/limitations.md`.

---
### Task 1: Gmail's batch calls, their cost, and the fake Gmail

**Files:**
- Modify: `backend/app/services/mail/adapter.py` (`PermanentDeleteRefused`, protocol methods)
- Modify: `backend/app/services/mail/gmail.py` (`BATCH_MAX`, `batch_delete`, `batch_trash`, `delete`)
- Modify: `backend/app/services/google/quota.py` (`UNIT_COSTS`)
- Modify: `backend/app/devtools/fake_gmail.py` (`batchDelete`, `batchModify`, `DELETE /messages/{id}`)
- Create: `backend/tests/test_gmail_batch.py`

**Interfaces:**
- Produces (in `app.services.mail.adapter`):
  - `class PermanentDeleteRefused(MailApiError)`: Google refused the full Gmail permission (status 403). Never a `MailAuthError`.
  - `MailAdapter.batch_delete(ids: list[str]) -> None`, `MailAdapter.batch_trash(ids: list[str]) -> None`, `MailAdapter.delete(id: str) -> None`
- Produces (in `app.services.mail.gmail`): `BATCH_MAX = 1000`, `PERMANENT_REFUSED_MSG`. `batch_delete` and `delete` raise `PermanentDeleteRefused` on Gmail's "insufficient permission"; all three raise `ValueError` for 0 or more than 1,000 ids or a bad id.
- Produces (quota): `UNIT_COSTS["gmail.messages.batchDelete"] == 50`, `["gmail.messages.batchModify"] == 50`, `["gmail.messages.delete"] == 10`.
- Produces (fake Gmail): `POST /messages/batchDelete` removes the ids it has and ignores the rest (204, more than 1,000 ids: 400). `POST /messages/batchModify` changes labels (adding `TRASH` drops `INBOX`, like the fake's trash), 400 when any id is unknown. `DELETE /messages/{id}` removes the mail (204), 404 when unknown. Each removal is a `messagesDeleted` history entry.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_gmail_batch.py
"""Bulk offload spec §11: batch_delete, batch_trash and delete, on the fake Gmail and in
the shape real Gmail expects."""

import json

import httpx
import pytest
import respx

from app.devtools.fake_gmail import FakeGmail, make_mail
from app.services.google.client import GoogleClient
from app.services.google.quota import UNIT_COSTS, ApiBudget
from app.services.mail.adapter import (
    MailApiError,
    MailAuthError,
    MailNotFound,
    PermanentDeleteRefused,
)
from app.services.mail.gmail import BATCH_MAX, GMAIL, GmailAdapter, StaticTokens
from tests.mail_helpers import fake_adapter


def _mailbox(n=3):
    fake = FakeGmail()
    ids = [fake.add(make_mail(f"m{i}"), labels=("INBOX",)) for i in range(n)]
    return fake, ids


def test_batch_calls_cost_50_units_and_one_delete_10():
    assert UNIT_COSTS["gmail.messages.batchDelete"] == 50
    assert UNIT_COSTS["gmail.messages.batchModify"] == 50
    assert UNIT_COSTS["gmail.messages.delete"] == 10


async def test_batch_delete_removes_mail_for_good_and_ignores_ids_gmail_lacks():
    fake, ids = _mailbox()
    start = fake.history_id
    await fake_adapter(fake).batch_delete([ids[0], ids[1]])
    assert set(fake.messages) == {ids[2]}
    await fake_adapter(fake).batch_delete([ids[0]])  # already gone: Gmail says nothing
    deleted = [h for h in fake.history if int(h["id"]) > start and "messagesDeleted" in h]
    assert len(deleted) == 2  # the keep-up check sees them like mail deleted in Gmail


async def test_batch_trash_moves_the_batch_to_the_trash_in_one_call():
    fake, ids = _mailbox()
    await fake_adapter(fake).batch_trash(ids)
    assert all("TRASH" in fake.messages[i].labels for i in ids)
    assert all("INBOX" not in fake.messages[i].labels for i in ids)
    posts = [c[1].rsplit("/", 1)[-1] for c in fake.calls if c[0] == "POST"]
    assert posts == ["batchModify"]


async def test_batch_trash_with_an_id_gmail_lacks_is_refused_and_changes_nothing():
    fake, ids = _mailbox()
    with pytest.raises(MailApiError) as e:
        await fake_adapter(fake).batch_trash([ids[0], "abc123"])
    assert e.value.status == 400
    assert "TRASH" not in fake.messages[ids[0]].labels


async def test_delete_removes_one_mail_and_says_when_gmail_has_none():
    fake, ids = _mailbox(1)
    await fake_adapter(fake).delete(ids[0])
    assert fake.messages == {}
    with pytest.raises(MailNotFound):
        await fake_adapter(fake).delete(ids[0])


@pytest.mark.parametrize("call", ["batch_delete", "delete"])
async def test_a_missing_full_permission_is_its_own_error_not_a_reconnect(call):
    fake, ids = _mailbox(1)
    fake.fail("POST", r"/batchDelete$", 403, reason="insufficientPermissions")
    fake.fail("DELETE", r"/messages/", 403, reason="insufficientPermissions")
    adapter = fake_adapter(fake)
    with pytest.raises(PermanentDeleteRefused) as e:
        if call == "batch_delete":
            await adapter.batch_delete(ids)
        else:
            await adapter.delete(ids[0])
    assert not isinstance(e.value, MailAuthError) and e.value.status == 403
    assert ids[0] in fake.messages


async def test_a_batch_takes_one_to_1000_good_ids():
    adapter = fake_adapter(FakeGmail())
    with pytest.raises(ValueError):
        await adapter.batch_delete([])
    with pytest.raises(ValueError):
        await adapter.batch_trash(["abc"] * (BATCH_MAX + 1))
    with pytest.raises(ValueError):
        await adapter.batch_delete(["../profile"])


def _real(budget):
    google = GoogleClient(budget=budget, pacer=None, http_factory=lambda: httpx.AsyncClient())
    return GmailAdapter(google, StaticTokens("ya29.test"), "gd_alice")


@respx.mock
async def test_the_requests_have_gmails_shape_and_cost(session_factory):
    budget = ApiBudget(session_factory, budget=lambda: 10_000)
    bd = respx.post(f"{GMAIL}/messages/batchDelete").mock(return_value=httpx.Response(204))
    bm = respx.post(f"{GMAIL}/messages/batchModify").mock(return_value=httpx.Response(204))
    one = respx.delete(f"{GMAIL}/messages/abc").mock(return_value=httpx.Response(204))
    adapter = _real(budget)
    await adapter.batch_delete(["abc", "def"])
    await adapter.batch_trash(["abc"])
    await adapter.delete("abc")
    assert json.loads(bd.calls[0].request.content) == {"ids": ["abc", "def"]}
    assert json.loads(bm.calls[0].request.content) == {
        "ids": ["abc"],
        "addLabelIds": ["TRASH"],
        "removeLabelIds": [],
    }
    assert one.called
    assert bd.calls[0].request.headers["Authorization"] == "Bearer ya29.test"
    assert budget.used_today() == 50 + 50 + 10
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_gmail_batch.py -q`
Expected: FAIL with `ImportError: cannot import name 'PermanentDeleteRefused'`.

- [ ] **Step 3: The error and the protocol**

In `backend/app/services/mail/adapter.py`, after `class MailUnreachable`, add:

```python
class PermanentDeleteRefused(MailApiError):
    """Google refused the full Gmail permission (bulk offload spec §3): the mailbox no
    longer allows permanent delete. Not a reason to reconnect mail."""
```

In `class MailAdapter(Protocol)`, after `untrash`, add:

```python
    async def batch_delete(self, ids: list[str]) -> None: ...

    async def batch_trash(self, ids: list[str]) -> None: ...

    async def delete(self, id: str) -> None: ...
```

- [ ] **Step 4: The costs**

In `backend/app/services/google/quota.py`, in `UNIT_COSTS` after `"gmail.messages.modify": 5,`, add:

```python
    # Bulk offload (spec §4): one call for up to 1,000 mails.
    "gmail.messages.batchDelete": 50,
    "gmail.messages.batchModify": 50,
    "gmail.messages.delete": 10,
```

- [ ] **Step 5: The adapter's calls**

In `backend/app/services/mail/gmail.py`, add `PermanentDeleteRefused` to the import from `app.services.mail.adapter`. After `API_OFF_REASONS`, add:

```python
# Gmail takes at most 1,000 ids in one batchDelete or batchModify.
BATCH_MAX = 1000
PERMANENT_REFUSED_MSG = "Google no longer lets CleanSync delete mail from this Gmail for good."
```

After `_mail_id`, add:

```python
def _batch_ids(ids: list[str]) -> list[str]:
    out = [_mail_id(i) for i in ids]
    if not 0 < len(out) <= BATCH_MAX:
        raise ValueError(f"a batch takes 1 to {BATCH_MAX} mail ids")
    return out
```

In `GmailAdapter`, after `untrash`, add:

```python
    async def _full(self, op: str, method: str, path: str, **kwargs) -> httpx.Response:
        """A call that needs the full Gmail permission. Gmail's "insufficient permission"
        means only that permission is gone, not the mail permission."""
        try:
            return await self._call(op, method, path, **kwargs)
        except MailAuthError as e:
            if e.login:
                raise
            raise PermanentDeleteRefused(PERMANENT_REFUSED_MSG, 403) from None

    async def batch_delete(self, ids: list[str]) -> None:
        """Remove mail from Gmail for good (bulk offload spec §4). Gmail says nothing about
        ids it no longer has."""
        body = {"ids": _batch_ids(ids)}
        await self._full("gmail.messages.batchDelete", "POST", "/messages/batchDelete", json=body)

    async def batch_trash(self, ids: list[str]) -> None:
        """Move mail to Gmail's trash in one call (bulk offload spec §4)."""
        body = {"ids": _batch_ids(ids), "addLabelIds": ["TRASH"], "removeLabelIds": []}
        await self._call("gmail.messages.batchModify", "POST", "/messages/batchModify", json=body)

    async def delete(self, id: str) -> None:
        """Remove one mail from Gmail for good (the fallback, and a rule's Delete forever)."""
        await self._full("gmail.messages.delete", "DELETE", f"/messages/{_mail_id(id)}")
```

- [ ] **Step 6: The fake Gmail**

In `backend/app/devtools/fake_gmail.py`, in `handle`, right after `if rest == "/history":` and its `return`, add:

```python
        if rest == "/messages/batchDelete" and request.method == "POST":
            ids = [str(x) for x in json.loads(request.content or b"{}").get("ids") or []]
            if not ids or len(ids) > 1000:
                return self._error(400, "invalidArgument")
            for mid in ids:
                if mid in self.messages:  # Gmail ignores ids it doesn't have
                    self.remove(mid)
            return httpx.Response(204)
        if rest == "/messages/batchModify" and request.method == "POST":
            body = json.loads(request.content or b"{}")
            ids = [str(x) for x in body.get("ids") or []]
            if not ids or len(ids) > 1000 or any(i not in self.messages for i in ids):
                return self._error(400, "invalidArgument")
            add = [str(x) for x in body.get("addLabelIds") or []]
            drop = {str(x) for x in body.get("removeLabelIds") or []}
            if "TRASH" in add:
                drop.add("INBOX")  # as the fake's messages.trash does
            for mid in ids:
                kept = [x for x in self.messages[mid].labels if x not in drop]
                self.set_labels(mid, kept + [x for x in add if x not in kept])
            return httpx.Response(204)
```

In the same method, right after `if request.method == "GET": return self._get(...)`, add:

```python
        if request.method == "DELETE" and m.group(2) is None:
            self.remove(msg.id)
            return httpx.Response(204)
```

- [ ] **Step 7: Run the tests**

Run: `cd backend && uv run pytest tests/test_gmail_batch.py tests/test_gmail_adapter.py tests/test_gmail_modify.py tests/test_google_quota.py -q`
Expected: PASS.

- [ ] **Step 8: Run the full backend suite and lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/mail/adapter.py backend/app/services/mail/gmail.py backend/app/services/google/quota.py backend/app/devtools/fake_gmail.py backend/tests/test_gmail_batch.py
git commit -m "feat(mail): Gmail's batchDelete, batchModify and delete, counted at 50, 50 and 10 units

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Migration 0013 and the permission rules

**Files:**
- Create: `backend/app/database/migrations/versions/0013_permanent_delete.py`
- Modify: `backend/app/models/accounts.py` (`permanent_delete`, `permanent_delete_note`)
- Modify: `backend/app/models/mail.py` (`MailOffload.kind`, `removed`, `stop_asked`)
- Modify: `backend/app/models/enums.py` (`MailOffloadKind`, `MailJobKind.DELETE_FOREVER`)
- Modify: `backend/app/services/accounts/oauth.py` (`FULL_MAIL_SCOPE`)
- Create: `backend/app/services/accounts/permanent.py`
- Modify: `backend/app/services/accounts/services.py` (`mail_needs_reconnect` drops the full scope too)
- Create: `backend/tests/test_migration_0013.py`, `backend/tests/test_permanent_delete.py`
- Modify: `backend/tests/test_database.py` (head is 0013)

**Interfaces:**
- Consumes: migration `0012` (round 2) as the head before this one.
- Produces:
  - `oauth.FULL_MAIL_SCOPE = "https://mail.google.com/"`
  - `Account.permanent_delete: bool` (default False), `Account.permanent_delete_note: str | None`
  - `MailOffload.kind: str` (default `"offload"`), `MailOffload.removed: int` (default 0), `MailOffload.stop_asked: bool` (default False)
  - `MailOffloadKind(StrEnum)`: `OFFLOAD = "offload"`, `DELETE_FOREVER = "delete_forever"`
  - `MailJobKind.DELETE_FOREVER = "DELETE_FOREVER"`
  - In `app.services.accounts.permanent`: `NOT_ALLOWED_MSG`, `LOST_MSG`, `permanent_delete_on(acc: Account | None) -> bool`, `allows_permanent_delete(db: Session, account_id: int) -> bool`, `turn_off(db: Session, account_id: int) -> None`, `permission_lost(db: Session, bus: EventBus, account_id: int) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_migration_0013.py
"""Migration 0013 (bulk offload spec §3, §5): the permanent delete switch, and what kind
of job an offload row is."""

from sqlalchemy import text

from app.database.engine import make_engine
from app.database.migrate import run_migrations
from tests.test_mail_speed_schema import _downgrade

STAMP = "2026-01-02 03:04:05.000000"


def _db(tmp_path):
    f = tmp_path / "v.db"
    run_migrations(f, "0012")
    with make_engine(f).begin() as c:
        c.execute(
            text(
                "INSERT INTO accounts (id, email, remote_name, status, connected_at) "
                "VALUES (1, 'a@x.com', 'a', 'CONNECTED', :t)"
            ),
            {"t": STAMP},
        )
        c.execute(
            text(
                "INSERT INTO mail_offloads (id, account_id, filter_text, state, total, done, "
                "skipped, bytes, created_at) VALUES (1, 1, 'older_than:1y', 'DONE', 3, 3, 0, "
                "30, :t)"
            ),
            {"t": STAMP},
        )
    return f


def _read(f, sql):
    with make_engine(f).connect() as c:
        return c.execute(text(sql)).all()


def test_existing_rows_get_the_safe_defaults(tmp_path):
    f = _db(tmp_path)
    run_migrations(f)
    assert _read(f, "SELECT permanent_delete, permanent_delete_note FROM accounts") == [(0, None)]
    assert _read(f, "SELECT kind, removed, stop_asked FROM mail_offloads") == [("offload", 0, 0)]


def test_downgrade_drops_the_columns_and_the_delete_forever_jobs(tmp_path):
    f = _db(tmp_path)
    run_migrations(f)
    with make_engine(f).begin() as c:
        c.execute(
            text(
                "INSERT INTO mail_offloads (id, account_id, filter_text, state, total, done, "
                "skipped, bytes, created_at, kind, removed, stop_asked) VALUES (2, 1, 'Inbox', "
                "'DONE', 1, 1, 0, 9, :t, 'delete_forever', 1, 0)"
            ),
            {"t": STAMP},
        )
    _downgrade(f, "0012")
    assert _read(f, "SELECT id FROM mail_offloads") == [(1,)]  # old code reads it as an offload
    assert "permanent_delete" not in [r[1] for r in _read(f, "PRAGMA table_info(accounts)")]
    assert "kind" not in [r[1] for r in _read(f, "PRAGMA table_info(mail_offloads)")]
```

```python
# backend/tests/test_permanent_delete.py
"""Bulk offload spec §3: permanent delete is on only with the switch and the scope."""

from sqlalchemy import select

from app.models import Event
from app.services.accounts.oauth import DRIVE_SCOPE, FULL_MAIL_SCOPE, GMAIL_SCOPE, has_scope
from app.services.accounts.permanent import (
    LOST_MSG,
    allows_permanent_delete,
    permanent_delete_on,
    permission_lost,
    turn_off,
)
from app.services.accounts.services import mail_needs_reconnect
from app.services.events.bus import EventBus


def _acc(db, make_account, scopes, on=True):
    acc = make_account()
    acc.scopes, acc.permanent_delete = scopes, on
    db.commit()
    return acc


def test_on_needs_the_switch_and_the_scope(db, make_account):
    acc = _acc(db, make_account, f"{DRIVE_SCOPE} {GMAIL_SCOPE}")
    assert permanent_delete_on(acc) is False  # the switch alone isn't enough
    acc.scopes += f" {FULL_MAIL_SCOPE}"
    db.commit()
    assert permanent_delete_on(acc) is True and allows_permanent_delete(db, acc.id) is True
    acc.permanent_delete = False
    db.commit()
    assert permanent_delete_on(acc) is False
    assert permanent_delete_on(None) is False and allows_permanent_delete(db, 999) is False


def test_turning_it_off_keeps_the_scope_and_clears_the_note(db, make_account):
    acc = _acc(db, make_account, f"{GMAIL_SCOPE} {FULL_MAIL_SCOPE}")
    acc.permanent_delete_note = LOST_MSG
    db.commit()
    turn_off(db, acc.id)
    db.refresh(acc)
    assert acc.permanent_delete is False and acc.permanent_delete_note is None
    assert has_scope(acc.scopes, FULL_MAIL_SCOPE)  # CleanSync doesn't revoke it at Google


def test_losing_it_turns_the_switch_off_once_with_a_note(db, make_account):
    acc = _acc(db, make_account, f"{GMAIL_SCOPE} {FULL_MAIL_SCOPE}")
    bus = EventBus()
    permission_lost(db, bus, acc.id)
    db.refresh(acc)
    assert acc.permanent_delete is False and acc.permanent_delete_note == LOST_MSG
    assert not has_scope(acc.scopes, FULL_MAIL_SCOPE) and has_scope(acc.scopes, GMAIL_SCOPE)
    permission_lost(db, bus, acc.id)  # again: nothing more to say
    events = db.scalars(select(Event).where(Event.kind == "permanent_delete_lost")).all()
    assert [e.message for e in events] == [f"{acc.email}: {LOST_MSG}"]
    assert events[0].level == "warning"


def test_losing_the_mail_permission_loses_the_full_one_too(db, make_account):
    acc = _acc(db, make_account, f"{DRIVE_SCOPE} {GMAIL_SCOPE} {FULL_MAIL_SCOPE}")
    mail_needs_reconnect(db, acc.id, "gone", permission_gone=True)
    db.refresh(acc)
    assert acc.scopes == DRIVE_SCOPE and permanent_delete_on(acc) is False
```

In `backend/tests/test_database.py`, the head assertions become `assert script.get_heads() == ["0013"]` and `assert script.get_revision("0013").down_revision == "0012"`, and the stamped version read after `run_migrations` becomes `"0013"`.

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_migration_0013.py tests/test_permanent_delete.py tests/test_database.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.accounts.permanent'`, and the migration tests fail on the missing columns.

- [ ] **Step 3: The migration**

```python
# backend/app/database/migrations/versions/0013_permanent_delete.py
"""Permanent delete per mailbox, and Delete forever jobs beside offloads

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as b:
        b.add_column(
            sa.Column("permanent_delete", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        b.add_column(sa.Column("permanent_delete_note", sa.Text(), nullable=True))
    with op.batch_alter_table("mail_offloads") as b:
        b.add_column(sa.Column("kind", sa.String(20), nullable=False, server_default="offload"))
        b.add_column(sa.Column("removed", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(
            sa.Column("stop_asked", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    # Older code would read a Delete forever job as an offload: those rows go.
    op.execute(sa.text("DELETE FROM mail_offloads WHERE kind = 'delete_forever'"))
    with op.batch_alter_table("mail_offloads") as b:
        for col in ("stop_asked", "removed", "kind"):
            b.drop_column(col)
    with op.batch_alter_table("accounts") as b:
        for col in ("permanent_delete_note", "permanent_delete"):
            b.drop_column(col)
```

- [ ] **Step 4: The models and enums**

In `backend/app/models/accounts.py`, import `Boolean` from `sqlalchemy` and add to `Account` after `scopes`:

```python
    # Allow permanent delete (bulk offload spec §3): the owner's switch. It counts only while
    # the scopes hold the full Gmail permission (app.services.accounts.permanent).
    permanent_delete: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Why it went off by itself (Google took the permission back); cleared by Turn on/off.
    permanent_delete_note: Mapped[str | None] = mapped_column(Text, nullable=True)
```

In `backend/app/models/mail.py`, import `Boolean` from `sqlalchemy` and add to `MailOffload` after `reason`:

```python
    kind: Mapped[str] = mapped_column(
        String(20), default=MailOffloadKind.OFFLOAD, server_default="offload"
    )
    removed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # for good
    stop_asked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
```

and import `MailOffloadKind` from `app.models.enums` there. Change the class docstring to `"""One offload (spec 5.3), or one Delete forever of mail in Gmail (bulk offload spec §5): a filter or a picked list, run in batches."""`.

In `backend/app/models/enums.py`, add `DELETE_FOREVER = "DELETE_FOREVER"  # mail in Gmail, gone everywhere` to `MailJobKind`, and after `MailOffloadState` add:

```python
class MailOffloadKind(StrEnum):
    OFFLOAD = "offload"  # out of Gmail, copy kept
    DELETE_FOREVER = "delete_forever"  # out of Gmail and CleanSync (bulk offload spec §5)
```

- [ ] **Step 5: The scope and the permission rules**

In `backend/app/services/accounts/oauth.py`, after `GMAIL_SCOPE`, add:

```python
# Read, send and permanently delete all mail (bulk offload spec §3). Asked for only when
# the owner turns on Allow permanent delete for a mailbox.
FULL_MAIL_SCOPE = "https://mail.google.com/"
```

```python
# backend/app/services/accounts/permanent.py
"""Allow permanent delete (bulk offload spec §3): opt-in per mailbox. On only while the
owner's switch is on and Google granted the full Gmail permission."""

from sqlalchemy.orm import Session

from app.models import Account
from app.services.accounts.oauth import FULL_MAIL_SCOPE, has_scope
from app.services.events.bus import EventBus, record_event

NOT_ALLOWED_MSG = "Turn on Allow permanent delete in Settings → Accounts."
LOST_MSG = "Permanent delete was turned off: Google no longer allows it for this mailbox."


def permanent_delete_on(acc: Account | None) -> bool:
    return acc is not None and bool(acc.permanent_delete) and has_scope(acc.scopes, FULL_MAIL_SCOPE)


def allows_permanent_delete(db: Session, account_id: int) -> bool:
    return permanent_delete_on(db.get(Account, account_id))


def turn_off(db: Session, account_id: int) -> None:
    """The owner's Turn off: CleanSync stops using it. Google keeps the permission until
    the owner removes it there (spec §3)."""
    acc = db.get(Account, account_id)
    if acc is not None:
        acc.permanent_delete, acc.permanent_delete_note = False, None
        db.commit()


def permission_lost(db: Session, bus: EventBus, account_id: int) -> None:
    """Google no longer allows it: the switch goes off with a note, the scope leaves the
    saved list (so Turn on asks Google again), and Activity says so once."""
    acc = db.get(Account, account_id)
    if acc is None:
        return
    was_on = bool(acc.permanent_delete)
    acc.permanent_delete = False
    acc.scopes = " ".join(s for s in acc.scopes.split() if s != FULL_MAIL_SCOPE)
    if was_on:
        acc.permanent_delete_note = LOST_MSG
    db.commit()
    if was_on:
        record_event(
            db,
            bus,
            kind="permanent_delete_lost",
            level="warning",
            message=f"{acc.email}: {LOST_MSG}",
            account_id=acc.id,
        )
        bus.publish("account", {"account_id": acc.id, "status": acc.status})
```

In `backend/app/services/accounts/services.py`, import `FULL_MAIL_SCOPE` beside `GMAIL_SCOPE`, and in `mail_needs_reconnect` change the scopes line to:

```python
            gone = {GMAIL_SCOPE, FULL_MAIL_SCOPE}  # without mail, no permanent delete either
            acc.scopes = " ".join(s for s in acc.scopes.split() if s not in gone)
```

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest tests/test_migration_0013.py tests/test_permanent_delete.py tests/test_database.py tests/test_account_services.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full backend suite and lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/database/migrations/versions/0013_permanent_delete.py backend/app/models backend/app/services/accounts backend/tests/test_migration_0013.py backend/tests/test_permanent_delete.py backend/tests/test_database.py
git commit -m "feat(accounts): migration 0013, the permanent delete switch and its rules

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Turning permanent delete on and off

**Files:**
- Modify: `backend/app/services/accounts/oauth.py` (`PERMANENT_PURPOSE`, `build_auth_url(scope=)`)
- Modify: `backend/app/services/accounts/service.py` (`start_connect` scopes, `start_permanent_delete`, `_follow_permanent_delete`)
- Modify: `backend/app/api/accounts.py` (the endpoint, the callback's redirect, `account_out`)
- Modify: `backend/app/schemas/accounts.py` (`AccountOut`, `PermanentDeleteIn`)
- Create: `backend/tests/test_permanent_delete_api.py`

**Interfaces:**
- Consumes: Task 2's `FULL_MAIL_SCOPE`, `permanent_delete_on`, `permission_lost`, `turn_off`, `LOST_MSG`.
- Produces:
  - `oauth.PERMANENT_PURPOSE = "permanent_delete"`; `build_auth_url(client_id, redirect_uri, state, challenge, scope: str = SCOPES)`
  - `AccountService.start_permanent_delete(account_id: int) -> str | None` (None: on now, no sign-in needed)
  - `POST /api/accounts/{account_id}/permanent-delete` with `{"on": bool}` gives `{"auth_url": str | null}`; 404 unknown account; 400 with a plain message.
  - `GET /api/accounts` rows gain `permanent_delete: bool` (on means both) and `permanent_delete_note: str | null`.
  - The callback after a Turn on sign-in redirects to `/?connected=<id>&permanent=on` or `&permanent=denied`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_permanent_delete_api.py
"""Settings → Accounts → Allow permanent delete (bulk offload spec §3)."""

from urllib.parse import parse_qs, urlparse

import respx

from app.models import Account
from app.models.enums import ServiceKind, ServiceState
from app.services.accounts.oauth import DRIVE_SCOPE, FULL_MAIL_SCOPE, GMAIL_SCOPE
from app.services.accounts.permanent import LOST_MSG
from app.services.accounts.services import set_service_state
from tests.test_mail_setup import _account, _google

BOTH = f"{DRIVE_SCOPE} {GMAIL_SCOPE}"


def _mail_ready(app, make_account, scopes=BOTH, on=False):
    acc = _account(app, make_account)
    with app.state.session_factory() as db:
        row = db.get(Account, acc.id)
        row.scopes, row.permanent_delete = scopes, on
        db.commit()
        set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    return acc


def _switch(auth_client, acc, on: bool):
    return auth_client.post(f"/api/accounts/{acc.id}/permanent-delete", json={"on": on})


def _query(url):
    return parse_qs(urlparse(url).query)


def _me(auth_client):
    return auth_client.get("/api/accounts").json()[0]


@respx.mock
def test_turn_on_asks_google_for_the_full_permission_then_it_is_on(auth_client, app, make_account):
    acc = _mail_ready(app, make_account)
    r = _switch(auth_client, acc, True)
    assert r.status_code == 200, r.text
    q = _query(r.json()["auth_url"])
    assert {FULL_MAIL_SCOPE, GMAIL_SCOPE, DRIVE_SCOPE} <= set(q["scope"][0].split())
    _google(f"{BOTH} {FULL_MAIL_SCOPE}")
    back = auth_client.get(
        f"/api/oauth/callback?state={q['state'][0]}&code=c", follow_redirects=False
    )
    assert back.headers["location"] == f"/?connected={acc.id}&permanent=on"
    me = _me(auth_client)
    assert me["permanent_delete"] is True and me["permanent_delete_note"] is None
    assert me["services"]["mail"] == "active"


@respx.mock
def test_a_sign_in_without_the_box_ticked_leaves_it_off(auth_client, app, make_account):
    acc = _mail_ready(app, make_account)
    q = _query(_switch(auth_client, acc, True).json()["auth_url"])
    _google(BOTH)
    back = auth_client.get(
        f"/api/oauth/callback?state={q['state'][0]}&code=c", follow_redirects=False
    )
    assert back.headers["location"] == f"/?connected={acc.id}&permanent=denied"
    assert _me(auth_client)["permanent_delete"] is False
    assert _me(auth_client)["services"]["mail"] == "active"  # Mail keeps working


def test_a_plain_reconnect_never_asks_for_it(auth_client, app, make_account):
    acc = _mail_ready(app, make_account)
    url = auth_client.post(f"/api/accounts/{acc.id}/reconnect").json()["auth_url"]
    assert FULL_MAIL_SCOPE not in _query(url)["scope"][0].split()


def test_when_google_already_allows_it_no_sign_in_is_needed(auth_client, app, make_account):
    acc = _mail_ready(app, make_account, f"{BOTH} {FULL_MAIL_SCOPE}")
    assert _switch(auth_client, acc, True).json() == {"auth_url": None}
    assert _me(auth_client)["permanent_delete"] is True
    assert _switch(auth_client, acc, False).json() == {"auth_url": None}
    me = _me(auth_client)
    assert me["permanent_delete"] is False and me["permanent_delete_note"] is None
    with app.state.session_factory() as db:
        assert FULL_MAIL_SCOPE in db.get(Account, acc.id).scopes.split()  # not revoked


@respx.mock
def test_a_reconnect_that_lost_the_permission_turns_it_off_with_a_note(
    auth_client, app, make_account
):
    acc = _mail_ready(app, make_account, f"{BOTH} {FULL_MAIL_SCOPE}", on=True)
    url = auth_client.post(f"/api/accounts/{acc.id}/reconnect").json()["auth_url"]
    _google(BOTH)  # the owner removed it at Google meanwhile
    auth_client.get(
        f"/api/oauth/callback?state={_query(url)['state'][0]}&code=c", follow_redirects=False
    )
    me = _me(auth_client)
    assert me["permanent_delete"] is False and me["permanent_delete_note"] == LOST_MSG


def test_it_needs_mail_set_up(auth_client, app, make_account):
    acc = _account(app, make_account)  # Mail not set up
    r = _switch(auth_client, acc, True)
    assert r.status_code == 400
    assert r.json()["detail"] == "Set up mail backup for this account first."
    assert auth_client.post("/api/accounts/999/permanent-delete", json={"on": True}).status_code == 404
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_permanent_delete_api.py -q`
Expected: FAIL: the endpoint answers 404/405 and `permanent_delete` is missing from the accounts list.

- [ ] **Step 3: The sign-in asks for the scope only on Turn on**

In `backend/app/services/accounts/oauth.py`, after `SCOPES`, add:

```python
PERMANENT_PURPOSE = "permanent_delete"  # a sign-in for Turn on (bulk offload spec §3)
```

Change `PendingAuth.purpose`'s comment to `# "connect", "mail" (set up Mail) or "permanent_delete"`. Change `build_auth_url` to take the scope:

```python
def build_auth_url(
    client_id: str, redirect_uri: str, state: str, challenge: str, scope: str = SCOPES
) -> str:
```

and in its `urlencode` dict use `"scope": scope,`.

In `backend/app/services/accounts/service.py`, import `FULL_MAIL_SCOPE` and `PERMANENT_PURPOSE` from `app.services.accounts.oauth`, `permission_lost` from `app.services.accounts.permanent`, and `service_state` from `app.services.accounts.services`. In `start_connect`, change the `return build_auth_url(...)` to:

```python
        scope = f"{SCOPES} {FULL_MAIL_SCOPE}" if purpose == PERMANENT_PURPOSE else SCOPES
        return build_auth_url(
            self.settings.google_client_id,
            self.settings.oauth_redirect_uri,
            state,
            challenge,
            scope,
        )
```

Add after `start_mail_setup`:

```python
    def start_permanent_delete(self, account_id: int) -> str | None:
        """Turn on Allow permanent delete (bulk offload spec §3). Google is asked for the full
        Gmail permission only when it hasn't granted it yet; a pretend local account gets it
        without Google. None: it is on now."""
        with self.session_factory() as db:
            acc = db.get(Account, account_id)
            if acc is None:
                raise LookupError("account not found")
            if acc.status != AccountStatus.CONNECTED:
                raise AccountError("Connect this Google account again first (Settings, Accounts).")
            if service_state(db, acc.id, ServiceKind.MAIL) == ServiceState.NOT_SET_UP:
                raise AccountError("Set up mail backup for this account first.")
            if is_pretend(acc, self.settings.allow_local_accounts):
                acc.scopes = " ".join(sorted(set(acc.scopes.split()) | {FULL_MAIL_SCOPE}))
            if has_scope(acc.scopes, FULL_MAIL_SCOPE):
                acc.permanent_delete, acc.permanent_delete_note = True, None
                db.commit()
                self.bus.publish("account", {"account_id": acc.id, "status": acc.status})
                return None
        return self.start_connect(account_id, purpose=PERMANENT_PURPOSE)

    def _follow_permanent_delete(self, db: Session, acc: Account, purpose: str) -> None:
        """After any sign-in: Turn on's sign-in that granted the scope turns the switch on;
        a sign-in without it, while the switch was on, means Google took it back."""
        if has_scope(acc.scopes, FULL_MAIL_SCOPE):
            if purpose == PERMANENT_PURPOSE:
                acc.permanent_delete, acc.permanent_delete_note = True, None
                db.commit()
        elif acc.permanent_delete:
            permission_lost(db, self.bus, acc.id)
```

In `finish_connect`, right after `self._follow_mail_permission(db, acc, pending.purpose)`, add:

```python
            self._follow_permanent_delete(db, acc, pending.purpose)
```

- [ ] **Step 4: The endpoint and the callback**

In `backend/app/schemas/accounts.py`, add to `AccountOut` after `mail_permission`:

```python
    permanent_delete: bool = False  # on means: the switch is on and Google allows it
    permanent_delete_note: str | None = None
```

and add:

```python
class PermanentDeleteIn(BaseModel):
    on: bool
```

In `backend/app/api/accounts.py`, import `PermanentDeleteIn` from `app.schemas.accounts`, `PERMANENT_PURPOSE` from `app.services.accounts.oauth`, and `permanent_delete_on, turn_off` from `app.services.accounts.permanent`. In `account_out`, add `"permanent_delete": permanent_delete_on(acc),` to the `update` dict. Add after `mail_setup`:

```python
@router.post("/api/accounts/{account_id}/permanent-delete", response_model=MailSetupOut)
def permanent_delete(
    account_id: int,
    body: PermanentDeleteIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Allow permanent delete, per mailbox (bulk offload spec §3). auth_url: Google's
    sign-in that asks for the full Gmail permission; None when nothing more is needed."""
    if db.get(Account, account_id) is None:
        raise HTTPException(404, "account not found")
    if not body.on:
        turn_off(db, account_id)
        request.app.state.bus.publish("account", {"account_id": account_id})
        return MailSetupOut(auth_url=None)
    try:
        return MailSetupOut(
            auth_url=request.app.state.accounts.start_permanent_delete(account_id)
        )
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except (AccountError, OAuthError) as e:
        raise HTTPException(400, str(e)) from e
```

In `oauth_callback`, before `return RedirectResponse(f"/?connected={acc.id}", 303)`, add:

```python
    if purpose == PERMANENT_PURPOSE:
        outcome = "on" if permanent_delete_on(acc) else "denied"
        return RedirectResponse(f"/?connected={acc.id}&permanent={outcome}", 303)
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/test_permanent_delete_api.py tests/test_mail_setup.py tests/test_accounts.py tests/test_oauth.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full backend suite and lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/accounts backend/app/api/accounts.py backend/app/schemas/accounts.py backend/tests/test_permanent_delete_api.py
git commit -m "feat(accounts): turn Allow permanent delete on and off per mailbox

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 4: Offload in bulk

**Files:**
- Modify: `backend/app/services/mail/offload.py` (the batch loop, the space counts, the restore summary)
- Modify: `backend/app/schemas/mail.py` (`MailOffloadOut.kind`, `removed`, `trashed`)
- Modify: `backend/app/api/mail_offload.py` (the Batches list and Recovery show offloads only)
- Create: `backend/tests/test_mail_bulk_offload.py`
- Modify: `backend/tests/test_mail_offload_run.py`, `backend/tests/test_mail_restore.py`

**Interfaces:**
- Consumes: Task 1's `batch_delete`, `batch_trash`, `delete`, `PermanentDeleteRefused`; Task 2's `allows_permanent_delete`, `permission_lost`, `MailOffload.removed`, `MailOffload.kind`, `MailOffloadKind`.
- Produces (in `app.services.mail.offload`):
  - `GMAIL_BATCH = 1000`, `UNREACHABLE_WAITS = (5.0, 20.0)`, `SKIP_NOT_DELETED`
  - `MailOffloads.sleep` (defaults to `asyncio.sleep`; tests replace it)
  - `MailOffloads._permanent(account_id: int) -> bool`, `MailOffloads._permission_lost(account_id: int) -> None`
  - `MailOffloads._batch_call(adapter, ids: list[str], permanent: bool) -> None` (retries `MailUnreachable`)
  - `MailOffloads._mark_offloaded(off_id: int, done: list[tuple[int, int]], *, removed: bool) -> None` (pairs of item id and mail row id)
  - `offload_out(...)` gains `"kind"`, `"removed"`, `"trashed"`.
- Produces: `GET /api/mail/offloads` and `GET /api/recovery/mail` list only `kind == "offload"`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mail_bulk_offload.py
"""Offload in bulk (bulk offload spec §4): batches of up to 1,000 mails, one Gmail call
each, the copy here checked first."""

import pytest
from sqlalchemy import select

from app.models import Account, Event, MailOffload, MailOffloadItem
from app.models.enums import MailItemResult, MailOffloadState, MailState, ServiceKind, ServiceState
from app.services.accounts.oauth import FULL_MAIL_SCOPE
from app.services.accounts.permanent import LOST_MSG
from app.services.accounts.services import service_state
from app.services.google.client import RateLimited
from app.services.google.quota import ApiBudget
from app.services.mail import offload as offload_mod
from app.services.mail.store import eml_path
from tests.mail_helpers import fake_adapter
from tests.test_mail_api import OneAdapter
from tests.test_mail_offload_run import OLD, _start


def allow_permanent(box, db):
    acc = db.get(Account, box.acc.id)
    acc.scopes = f"{acc.scopes} {FULL_MAIL_SCOPE}".strip()
    acc.permanent_delete = True
    db.commit()


class Recording:
    """The real adapter, remembering the ids of each batch call."""

    def __init__(self, inner):
        self.inner, self.sent = inner, []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def batch_trash(self, ids):
        self.sent.append(("trash", list(ids)))
        await self.inner.batch_trash(ids)

    async def batch_delete(self, ids):
        self.sent.append(("delete", list(ids)))
        await self.inner.batch_delete(ids)


class LostAnswer(Recording):
    """Gmail does the first batch, but CleanSync never records it: the run pauses right after
    (a restart does the same, see Task 5)."""

    def __init__(self, inner):
        super().__init__(inner)
        self.lost = False

    async def _then_lose(self):
        if not self.lost:
            self.lost = True
            raise RateLimited()

    async def batch_trash(self, ids):
        await super().batch_trash(ids)
        await self._then_lose()

    async def batch_delete(self, ids):
        await super().batch_delete(ids)
        await self._then_lose()


def _use(box, adapter):
    box.mail.adapters = OneAdapter(adapter)
    return adapter


def _posts(box, name):
    return [c for c in box.fake.calls if c[0] == "POST" and c[1].endswith(f"/{name}")]


def _detail(auth_client, off_id):
    return auth_client.get(f"/api/mail/offloads/{off_id}").json()


async def _offload_old(auth_client, box):
    await box.backup()
    r = _start(auth_client, box.acc, 3, q="older_than:1y")
    assert r.status_code == 202, r.text
    await box.run_queued()
    return r.json()["offload_id"]


async def test_with_permanent_delete_one_call_removes_the_batch_for_good(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    off_id = await _offload_old(auth_client, box)
    assert len(_posts(box, "batchDelete")) == 1 and _posts(box, "batchModify") == []
    assert not [c for c in box.fake.calls if c[1].endswith("/trash")]
    for s in OLD:
        row = box.row(s)
        assert row.state == MailState.OFFLOADED and row.offload_id == off_id
        assert row.provider_id not in box.fake.messages
    d = _detail(auth_client, off_id)
    assert d["state"] == "DONE" and d["kind"] == "offload"
    assert (d["done"], d["removed"], d["trashed"]) == (3, 3, 0)


async def test_without_it_one_call_moves_the_batch_to_the_trash(auth_client, offload_box):
    box = offload_box
    off_id = await _offload_old(auth_client, box)
    assert len(_posts(box, "batchModify")) == 1 and _posts(box, "batchDelete") == []
    assert all("TRASH" in box.fake.messages[box.row(s).provider_id].labels for s in OLD)
    d = _detail(auth_client, off_id)
    assert (d["done"], d["removed"], d["trashed"]) == (3, 0, 3)


async def test_batches_hold_at_most_the_batch_size(auth_client, offload_box, monkeypatch):
    monkeypatch.setattr(offload_mod, "GMAIL_BATCH", 2)
    box = offload_box
    await box.backup()
    rec = _use(box, Recording(box.mail.adapters.adapter))
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    await box.run_queued()
    assert [len(ids) for _, ids in rec.sent] == [2, 1]


async def test_a_copy_that_fails_is_skipped_and_never_sent(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    bad = box.row("Old photos")
    path = eml_path(box.app.state.layout, box.acc.email, bad.file_path)
    path.write_bytes(path.read_bytes()[:-1] + b"!")  # same size, other contents
    rec = _use(box, Recording(box.mail.adapters.adapter))
    await box.run_queued()
    sent = [pid for _, ids in rec.sent for pid in ids]
    assert bad.provider_id not in sent and len(sent) == 2
    assert bad.provider_id in box.fake.messages
    assert box.row("Old photos").state == MailState.IN_MAIL
    off = db.query(MailOffload).one()
    assert (off.done, off.skipped) == (2, 1)
    item = db.query(MailOffloadItem).filter_by(message_id=bad.id).one()
    assert item.reason == (
        "The copy on this server didn't check out (the file's contents changed), "
        "so it stayed in Gmail."
    )


async def test_a_refused_batch_goes_one_at_a_time_and_only_the_bad_mail_stays(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    bad = box.row("Old statement").provider_id
    box.fake.fail("POST", r"/batchDelete$", 400)
    box.fake.fail("DELETE", rf"/messages/{bad}$", 400)
    await box.run_queued()
    assert box.row("Old statement").state == MailState.IN_MAIL and bad in box.fake.messages
    for s in ("Old photos", "Ancient note"):
        assert box.row(s).state == MailState.OFFLOADED
        assert box.row(s).provider_id not in box.fake.messages
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.DONE_WITH_SKIPS
    assert (off.done, off.skipped, off.removed) == (2, 1, 2)
    item = db.query(MailOffloadItem).filter_by(result=MailItemResult.SKIPPED).one()
    assert item.reason.startswith("Gmail didn't delete it (")


async def test_in_the_fallback_a_mail_gmail_no_longer_has_counts_as_offloaded(
    auth_client, offload_box, db
):
    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    box.fake.remove(box.row("Ancient note").provider_id)  # deleted in Gmail meanwhile
    await box.run_queued()  # batchModify refuses the unknown id; one at a time, trash says 404
    assert {box.row(s).state for s in OLD} == {MailState.OFFLOADED}
    off = db.query(MailOffload).one()
    assert (off.done, off.skipped, off.removed) == (3, 0, 1)


async def test_gmails_server_trouble_is_tried_again_after_a_pause(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    pauses: list[float] = []

    async def nap(s):
        pauses.append(s)

    box.offloads.sleep = nap
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    box.fake.fail("POST", r"/batchDelete$", 503, times=2)
    await box.run_queued()
    assert pauses == [5.0, 20.0]
    assert db.query(MailOffload).one().done == 3


async def test_server_trouble_that_lasts_fails_the_offload_and_leaves_the_mail(
    auth_client, offload_box, db
):
    box = offload_box

    async def nap(s):
        return None

    box.offloads.sleep = nap
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    box.fake.fail("POST", r"/batchModify$", 503, times=3)
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.FAILED
    assert off.reason.startswith("CleanSync couldn't reach Gmail")
    assert {box.row(s).state for s in OLD} == {MailState.IN_MAIL}


async def test_quota_is_counted_per_call_not_per_mail(auth_client, offload_box, db, session_factory):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    budget = ApiBudget(session_factory, budget=lambda: 10_000)
    box.mail.adapters = OneAdapter(fake_adapter(box.fake, budget=budget))
    box.mail.budget = budget
    await box.run_queued()
    assert budget.used_today() == 50  # one batchDelete for the three mails


@pytest.mark.parametrize("permanent", [True, False])
async def test_a_batch_gmail_did_but_cleansync_never_recorded_is_sent_again_and_counted_once(
    auth_client, offload_box, db, permanent
):
    box = offload_box
    if permanent:
        allow_permanent(box, db)
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    _use(box, LostAnswer(box.mail.adapters.adapter))
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.WAITING and off.done == 0
    await box.backup()  # a check sees them gone (or in the trash) before the offload goes on
    assert {box.row(s).state for s in OLD} == {MailState.DELETED}
    await box.mail.tick()
    await box.run_queued()
    db.refresh(off)
    assert off.state == MailOffloadState.DONE and off.skipped == 0
    assert (off.done, off.removed) == (3, 3 if permanent else 0)
    assert {box.row(s).state for s in OLD} == {MailState.OFFLOADED}


async def test_when_google_refuses_the_full_permission_the_batch_goes_to_the_trash(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    box.fake.fail("POST", r"/batchDelete$", 403, reason="insufficientPermissions")
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.DONE and (off.done, off.removed) == (3, 0)
    assert all("TRASH" in box.fake.messages[box.row(s).provider_id].labels for s in OLD)
    acc = db.get(Account, box.acc.id)
    db.refresh(acc)
    assert acc.permanent_delete is False and acc.permanent_delete_note == LOST_MSG
    assert service_state(db, box.acc.id, ServiceKind.MAIL) == ServiceState.ACTIVE
    assert db.scalar(select(Event).where(Event.kind == "permanent_delete_lost")) is not None


async def test_restore_after_a_permanent_delete_imports_the_mail_again(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    off_id = await _offload_old(auth_client, box)
    r = auth_client.post("/api/mail/restore", json={"account_id": box.acc.id, "offload_id": off_id})
    assert r.status_code == 202, r.text
    await box.run_queued()
    assert {box.row(s).state for s in OLD} == {MailState.IN_MAIL}
    jobs = auth_client.get("/api/activity/jobs").json()
    assert jobs[0]["message"] == "Put 3 mails back in Gmail (3 imported again)"
```

In `backend/tests/test_mail_offload_run.py`:
- Remove `SKIP_GONE` from the import of `app.services.mail.offload`.
- Replace `AfterTrash`, `_wrap` and `_trash_calls` with:

```python
class AfterTrash:
    """The real adapter, with something done right after each batch moved to the trash."""

    def __init__(self, inner, after):
        self.inner, self.after, self.batches = inner, after, 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def batch_trash(self, ids):
        await self.inner.batch_trash(ids)
        self.batches += 1
        self.after(ids, self.batches)


def _wrap(box, after):
    adapter = AfterTrash(box.mail.adapters.adapter, after)
    box.mail.adapters = OneAdapter(adapter)
    return adapter


def _trash_calls(box):
    """Every Gmail call that takes mail out of the mailbox (one per batch since 0.5.0)."""
    out = ("/trash", "/batchModify", "/batchDelete")
    return [
        c
        for c in box.fake.calls
        if (c[0] == "POST" and c[1].endswith(out)) or c[0] == "DELETE"
    ]
```

- In `test_an_offload_moves_mail_to_gmails_trash_checks_it_and_marks_it`, `assert len(_trash_calls(box)) == 3` becomes `assert len(_trash_calls(box)) == 1  # one batch`.
- Replace `test_an_offload_stopped_by_the_budget_waits_and_goes_on` with:

```python
async def test_an_offload_stopped_by_the_budget_waits_and_goes_on(
    auth_client, offload_box, session_factory, db, monkeypatch
):
    monkeypatch.setattr(offload_mod, "GMAIL_BATCH", 2)
    box = offload_box
    await box.backup()
    assert _start(auth_client, box.acc, 3, q="older_than:1y").status_code == 202
    day = {"now": datetime.now(UTC)}
    budget = ApiBudget(session_factory, budget=lambda: 60, clock=lambda: day["now"])
    box.mail.adapters = OneAdapter(fake_adapter(box.fake, budget=budget))
    box.mail.budget = budget
    # One batch costs 50: the second batch would go over 60, so it is never sent.
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.WAITING and off.done == 2
    assert "It goes on after midnight UTC" in off.reason
    jobs = auth_client.get("/api/activity/jobs").json()
    assert jobs[0]["kind"] == "OFFLOAD" and jobs[0]["state"] == "PAUSED"
    await box.mail.tick()  # still the same day: nothing is asked for
    assert box.queue.requests == []
    day["now"] += timedelta(days=1)
    box.mail.budget = ApiBudget(session_factory, budget=lambda: 10_000, clock=lambda: day["now"])
    box.mail.adapters = OneAdapter(fake_adapter(box.fake))
    await box.mail.tick()  # the next day's tick asks for it again
    assert (box.acc.id, "offload", {"offload_id": off.id}) in [r[:3] for r in box.queue.requests]
    await box.run_queued()
    db.refresh(off)
    assert off.state == MailOffloadState.DONE and off.done == 3 and off.skipped == 0
    assert {box.row(s).state for s in OLD} == {MailState.OFFLOADED}
```

- In `test_a_mail_gmail_refuses_to_trash_is_skipped_and_the_rest_go_on`, add `box.fake.fail("POST", r"/batchModify$", 400)  # the batch is refused: one at a time` right before `await box.run_queued()`.
- In `test_a_disk_gone_mid_run_fails_the_offload_and_skips_nothing`, add `monkeypatch` to the arguments and `monkeypatch.setattr(offload_mod, "GMAIL_BATCH", 1)` as its first line. The `_wrap` lambda becomes `lambda ids, n: shutil.rmtree(root) if n == 1 else None`.
- Delete `test_a_mail_a_check_marked_deleted_after_our_trash_is_claimed_on_resume` (now `test_a_batch_gmail_did_but_cleansync_never_recorded_is_sent_again_and_counted_once`) and `test_a_mail_gmail_loses_right_after_the_trash_call_says_so` (no check after the call any more: Gmail's answer is the check).
- Rename `test_the_minimum_age_is_read_for_each_mail` to `test_the_minimum_age_is_read_for_each_batch`, add `monkeypatch` to its arguments, add `monkeypatch.setattr(offload_mod, "GMAIL_BATCH", 1)` as its first line, and change its lambda to `lambda ids, n: set_settings(db, {"mail_min_age_days": 450}) if n == 1 else None`.
- In `test_refusals_in_a_row_stop_the_run`, add `box.fake.fail("POST", r"/batchModify$", 400)` before `box.fake.fail("POST", r"/trash$", 400, times=10)`.
- In `test_a_rate_limit_waits_and_goes_on`, `box.fake.fail("POST", r"/trash$", 429, times=8)` becomes `box.fake.fail("POST", r"/batchModify$", 429, times=8)`.

In `backend/tests/test_mail_restore.py`, the expected message becomes `"Put 3 mails back in Gmail (2 out of Gmail's trash, 1 imported again)"`.

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_mail_bulk_offload.py tests/test_mail_offload_run.py tests/test_mail_restore.py -q`
Expected: FAIL. Offloads still trash one mail at a time, `removed` and `trashed` are missing, and the restore summary says "imported".

- [ ] **Step 3: The batch loop**

In `backend/app/services/mail/offload.py`:

Change the module docstring to:

```python
"""Mail offload and restore (spec 5): out of Gmail by filter or by pick, a batch of up to
1,000 at a time, for good or into Gmail's trash (bulk offload spec §4), and back into Gmail
without ever making a second copy. Runs as mail queue actions, so never beside a backup of
the account."""
```

Add to the imports: `MailOffloadKind` from `app.models.enums`; `PermanentDeleteRefused` to the `app.services.mail.adapter` import; and

```python
from app.services.accounts.permanent import allows_permanent_delete, permission_lost
```

Remove the constants nothing uses any more: `SKIP_GONE`, `SKIP_OUTSIDE_TRASH`, `SKIP_UNCHECKED` and `SKIP_NOT_TRASHED`. `GONE` and `IN_TRASH` stay: the restore uses them. After `SKIP_REFUSED`, add:

```python
SKIP_NOT_DELETED = "Gmail didn't delete it ({error}), so it stayed in Gmail."
GMAIL_BATCH = 1000  # mails per Gmail call (bulk offload spec §4)
UNREACHABLE_WAITS = (5.0, 20.0)  # Gmail's server trouble: a batch call is tried again after these
```

In `restore_summary`, `detail.append(f"{c[IMPORTED]:,} imported")` becomes `detail.append(f"{c[IMPORTED]:,} imported again")`.

In `offload_out`, add to the returned dict:

```python
        "kind": off.kind,
        "removed": off.removed,
        "trashed": off.done - off.removed,
```

In `MailOffloads.__init__`, add `self.sleep = asyncio.sleep  # tests replace it`.

Replace everything from `def _mark_offloaded` through the end of `_check_trashed` (that is `_mark_offloaded`, `_offload_one` and `_check_trashed`) with:

```python
    def _permanent(self, account_id: int) -> bool:
        """Read before every call: Google may take the permission back during a run."""
        with self.sf() as db:
            return allows_permanent_delete(db, account_id)

    def _permission_lost(self, account_id: int) -> None:
        with self.sf() as db:
            permission_lost(db, self.bus, account_id)

    def _mark_offloaded(self, off_id: int, done: list[tuple[int, int]], *, removed: bool) -> None:
        """Only after Gmail's success answer (spec §4.4). `done`: item id and mail row id."""
        with self.sf() as db:
            off = db.get(MailOffload, off_id)
            for item_id, msg_id in done:
                row = db.get(MailMessage, msg_id)
                row.state, row.offload_id = MailState.OFFLOADED, off_id
                db.get(MailOffloadItem, item_id).result = MailItemResult.OFFLOADED
                off.done += 1
                off.bytes += row.size
                if removed:
                    off.removed += 1
            db.commit()

    async def _checked(
        self, acc: Account, off_id: int, item_id: int, log_path
    ) -> tuple[int, int, str] | None:
        """Spec 5.3 step 1, for one mail of a batch: (item id, mail row id, Gmail id) when it
        may leave Gmail, else None after skipping it with its reason. Trouble that is not this
        mail's (the disk) is raised."""
        with self.sf() as db:
            min_age = self._min_age(db)  # as set now: the owner may change it during a run
            item = db.get(MailOffloadItem, item_id)
            row = db.get(MailMessage, item.message_id)
            if row is None or row.account_id != acc.id:
                return self._skip(off_id, item_id, SKIP_NOT_IN_GMAIL, log_path)
            path = eml_path(self.layout, acc.email, row.file_path)
            state, date = row.state, row.date
            size, sha, pid, msg_id = row.file_size, row.sha256, row.provider_id, row.id
        # DELETED: this offload sent it before a pause or a restart, and a check saw it leave
        # Gmail (or reach the trash) before the offload recorded it. It is sent again: Gmail
        # takes a removal of mail it no longer has quietly (spec §4.4).
        if state not in (MailState.IN_MAIL, MailState.DELETED):
            return self._skip(off_id, item_id, SKIP_NOT_IN_GMAIL, log_path)
        if state == MailState.IN_MAIL and as_utc(date) > self.clock() - timedelta(days=min_age):
            return self._skip(off_id, item_id, SKIP_TOO_NEW.format(days=min_age), log_path)
        problem = await self._copy_problem(path, size, sha)
        if problem:
            return self._skip(off_id, item_id, SKIP_BAD_COPY.format(problem=problem), log_path)
        return item_id, msg_id, pid

    async def _batch_call(self, adapter, ids: list[str], permanent: bool) -> None:
        """One Gmail call for the batch. Gmail's server trouble is tried again after a pause;
        rate limits are waited out by the Google client itself."""
        for pause in (*UNREACHABLE_WAITS, None):
            try:
                if permanent:
                    await adapter.batch_delete(ids)
                else:
                    await adapter.batch_trash(ids)
                return
            except MailUnreachable:
                if pause is None:
                    raise
                await self.sleep(pause)

    async def _one_call(self, acc: Account, adapter, pid: str, permanent: bool) -> bool:
        """The fallback for one mail. True when it was removed for good."""
        if permanent:
            try:
                await adapter.delete(pid)
                return True
            except PermanentDeleteRefused:
                self._permission_lost(acc.id)
        await adapter.trash(pid)
        return False

    async def _one_by_one(
        self, acc: Account, adapter, off_id: int, ready: list[tuple[int, int, str]], log_path
    ) -> None:
        """Gmail refused the batch: one mail at a time, so one bad id can't block the rest.
        Gmail's answer is the check; "not found" means it is already out of Gmail."""
        refused: list[str] = []
        for item_id, msg_id, pid in ready:
            permanent = self._permanent(acc.id)
            try:
                removed = await self._one_call(acc, adapter, pid, permanent)
            except MailNotFound:
                removed = True
            except (MailAuthError, MailSetupProblem, MailUnreachable):
                raise
            except MailApiError as e:
                why = SKIP_NOT_DELETED if permanent else SKIP_REFUSED
                self._skip(off_id, item_id, why.format(error=e), log_path)
                refused.append(str(e))
                if len(refused) >= REFUSED_IN_A_ROW:
                    raise _Refusing(OFFLOAD_REFUSED_MSG.format(n=len(refused), error=e)) from e
                continue
            refused = []
            self._mark_offloaded(off_id, [(item_id, msg_id)], removed=removed)
            job_log(log_path, f"{'removed' if removed else 'trashed'} {pid}")

    async def _offload_batch(
        self, acc: Account, adapter, off_id: int, item_ids: list[int], log_path
    ) -> None:
        """Bulk offload spec §4: every copy of the batch checked, then one Gmail call for the
        mails whose copy checked out: batchDelete with permanent delete, batchModify to the
        trash without."""
        ready = []
        for item_id in item_ids:
            checked = await self._checked(acc, off_id, item_id, log_path)
            if checked is not None:
                ready.append(checked)
        if not ready:
            return
        ids = [pid for _, _, pid in ready]
        while True:
            permanent = self._permanent(acc.id)
            try:
                await self._batch_call(adapter, ids, permanent)
                break
            except PermanentDeleteRefused:
                self._permission_lost(acc.id)  # the same batch goes to the trash instead
            except (MailAuthError, MailSetupProblem, MailUnreachable):
                raise
            except MailApiError as e:
                job_log(log_path, f"Gmail refused the batch ({e}); one mail at a time instead")
                return await self._one_by_one(acc, adapter, off_id, ready, log_path)
        self._mark_offloaded(off_id, [(i, m) for i, m, _ in ready], removed=permanent)
        job_log(log_path, f"{'removed' if permanent else 'trashed'} {len(ids)} mails")
```

In `_run`, replace from `with self.sf() as db:` (the one reading `mail_batch_size`) to the end of the `while True:` loop with:

```python
            with self.sf() as db:
                total = db.get(MailOffload, off_id).total
                job = start_mail_job(
                    db, self.layout, self.bus, account_id, MailJobKind.OFFLOAD, trigger, total
                )
            adapter = self.mail.adapter_for(acc)
            while True:
                with self.sf() as db:
                    items = list(
                        db.scalars(
                            select(MailOffloadItem.id)
                            .where(
                                MailOffloadItem.offload_id == off_id,
                                MailOffloadItem.result == MailItemResult.PENDING,
                            )
                            .order_by(MailOffloadItem.id)
                            .limit(GMAIL_BATCH)
                        )
                    )
                if not items:
                    break
                if await self.mail.stopped():  # before every batch (spec 3.4)
                    raise Paused(self.mail.stop_message() or MISSING_DISK_MSG)
                await self._offload_batch(acc, adapter, off_id, items, job.log_path)
                with self.sf() as db:
                    off = db.get(MailOffload, off_id)
                    update_mail_job(db, job.id, done=off.done, bytes_=off.bytes)
                self._changed(account_id, off_id)  # one "mail" event per batch
```

(The `refused` list of the old loop goes: refusals in a row are counted inside `_one_by_one`.)

- [ ] **Step 4: Only offloads in the Batches tab and Recovery**

In `backend/app/api/mail_offload.py`, import `MailOffloadKind` from `app.models.enums`. In `list_offloads`, the `where` becomes `.where(MailOffload.account_id == account_id, MailOffload.kind == MailOffloadKind.OFFLOAD)`. In `recovery_mail`, the offloads' `where` becomes `.where(MailOffload.account_id == account_id, MailOffload.done > 0, MailOffload.kind == MailOffloadKind.OFFLOAD)`.

In `backend/app/schemas/mail.py`, add to `MailOffloadOut` after `google_emptied`:

```python
    kind: str = "offload"  # or "delete_forever" (bulk offload spec §5)
    removed: int = 0  # removed from Gmail for good: the space is free now
    trashed: int = 0  # in Gmail's trash until Gmail empties it
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/test_mail_bulk_offload.py tests/test_mail_offload_run.py tests/test_mail_restore.py tests/test_mail_pick_all.py tests/test_mail_offloaded_state.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full backend suite and lint**

Run: `cd backend && grep -n "_offload_one\|_check_trashed\|SKIP_GONE\|mail_batch_size" app/services/mail/offload.py; uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: the grep finds nothing. Tests and lint PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/mail/offload.py backend/app/schemas/mail.py backend/app/api/mail_offload.py backend/tests/test_mail_bulk_offload.py backend/tests/test_mail_offload_run.py backend/tests/test_mail_restore.py
git commit -m "feat(mail): offload in batches of 1,000, for good when the mailbox allows it

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Stop, and going on after a restart

**Files:**
- Modify: `backend/app/services/mail/offload.py` (`STOPPED_MSG`, `MailOffloads.stop`, the check before each batch)
- Modify: `backend/app/services/mail/offload_plan.py` (`recover_mail_offloads`, `RESTARTED_OFFLOAD_MSG`)
- Modify: `backend/app/api/mail_offload.py` (`POST /api/mail/offloads/{offload_id}/stop`)
- Create: `backend/tests/test_mail_offload_stop.py`
- Modify: `backend/tests/test_mail_offload_model.py`

**Interfaces:**
- Consumes: Task 2's `MailOffload.stop_asked`; Task 4's batch loop.
- Produces:
  - `offload.STOPPED_MSG = "Stopped by you. What wasn't offloaded yet stayed in Gmail."`
  - `MailOffloads.stop(off_id: int) -> None` (raises `LookupError` for an unknown id)
  - `POST /api/mail/offloads/{offload_id}/stop` gives 204, or 404.
  - `offload_plan.RESTARTED_OFFLOAD_MSG = "CleanSync restarted during this offload. It goes on by itself."` (replaces `INTERRUPTED_OFFLOAD_MSG`)

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mail_offload_stop.py
"""Stop on an offload (bulk offload spec §4.4, plan decision 9)."""

from app.models import MailOffload, MailOffloadItem
from app.models.enums import MailItemResult, MailOffloadState, MailState
from app.services.mail import offload as offload_mod
from app.services.mail.offload import STOPPED_MSG
from tests.test_mail_offload_run import OLD, _start, _trash_calls, _wrap


async def test_stop_before_it_runs_ends_it_and_nothing_leaves_gmail(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    r = auth_client.post(f"/api/mail/offloads/{off_id}/stop")
    assert r.status_code == 204
    off = db.get(MailOffload, off_id)
    db.refresh(off)
    assert off.state == MailOffloadState.FAILED and off.reason == STOPPED_MSG
    assert off.finished_at is not None
    await box.run_queued()  # the queued run finds it stopped
    assert _trash_calls(box) == [] and {box.row(s).state for s in OLD} == {MailState.IN_MAIL}
    detail = auth_client.get(f"/api/mail/offloads/{off_id}").json()
    assert detail["state"] == "FAILED" and detail["not_done"] == 3


async def test_stop_during_the_run_stops_before_the_next_batch(
    auth_client, offload_box, db, monkeypatch
):
    monkeypatch.setattr(offload_mod, "GMAIL_BATCH", 1)
    box = offload_box
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    _wrap(box, lambda ids, n: box.offloads.stop(off_id) if n == 1 else None)
    await box.run_queued()
    off = db.get(MailOffload, off_id)
    db.refresh(off)
    assert off.state == MailOffloadState.FAILED and off.reason == STOPPED_MSG
    assert off.done == 1 and len(_trash_calls(box)) == 1
    pending = db.query(MailOffloadItem).filter_by(result=MailItemResult.PENDING).count()
    assert pending == 2


async def test_stop_on_a_finished_offload_changes_nothing(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    await box.run_queued()
    assert auth_client.post(f"/api/mail/offloads/{off_id}/stop").status_code == 204
    assert db.get(MailOffload, off_id).state == MailOffloadState.DONE
    assert auth_client.post("/api/mail/offloads/999999/stop").status_code == 404
```

In `backend/tests/test_mail_offload_model.py`, change the import to `from app.services.mail.offload_plan import RESTARTED_OFFLOAD_MSG, recover_mail_offloads`, and replace `test_a_stop_fails_a_running_offload_but_a_waiting_one_keeps_waiting` with:

```python
def test_a_restart_puts_a_running_offload_back_to_waiting(db, make_account):
    """Bulk offload spec §4.4: a batch Gmail did but CleanSync never recorded is sent again."""
    acc = make_account()
    rows = {
        s: MailOffload(account_id=acc.id, filter_text="x", total=3, state=s)
        for s in (MailOffloadState.WAITING, MailOffloadState.RUNNING, MailOffloadState.DONE)
    }
    stopping = MailOffload(
        account_id=acc.id, filter_text="y", total=3, state=MailOffloadState.RUNNING, stop_asked=True
    )
    db.add_all([*rows.values(), stopping])
    db.commit()
    assert recover_mail_offloads(db) == 2
    db.expire_all()
    running = rows[MailOffloadState.RUNNING]
    assert running.state == MailOffloadState.WAITING and running.reason == RESTARTED_OFFLOAD_MSG
    assert running.finished_at is None
    assert stopping.state == MailOffloadState.FAILED and stopping.reason.startswith("Stopped by you.")
    for s in (MailOffloadState.WAITING, MailOffloadState.DONE):
        assert rows[s].state == s and rows[s].reason is None
```

In `test_at_startup_a_cut_short_offload_and_its_job_both_end_failed`, rename it to `test_at_startup_a_cut_short_offload_waits_and_its_job_is_interrupted`, change its docstring to `"""Activity and the offload's page agree: the offload waits to go on, its job says why."""`, and replace the three assertions inside `with TestClient(app):` that follow `db.expire_all()` with:

```python
        assert db.get(MailOffload, off.id).state == MailOffloadState.WAITING
        row = db.get(MailJob, job.id)
        assert row.state == JobState.INTERRUPTED and row.message == RESTARTED_OFFLOAD_MSG
        assert row.completed_at is not None
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_mail_offload_stop.py tests/test_mail_offload_model.py -q`
Expected: FAIL with `ImportError: cannot import name 'STOPPED_MSG'` and `cannot import name 'RESTARTED_OFFLOAD_MSG'`.

- [ ] **Step 3: Stop**

In `backend/app/services/mail/offload.py`, after `REST_STAYED`, add:

```python
STOPPED_MSG = f"Stopped by you. {REST_STAYED}"
```

Add to `MailOffloads`, after `resume_waiting`:

```python
    def stop(self, off_id: int) -> None:
        """Stop (plan decision 9): a waiting one ends now; a running one before its next
        batch. What wasn't reached stays in Gmail."""
        with self.sf() as db:
            off = db.get(MailOffload, off_id)
            if off is None:
                raise LookupError("That offload doesn't exist.")
            if off.state == MailOffloadState.WAITING:
                off.state, off.reason, off.finished_at = (
                    MailOffloadState.FAILED,
                    STOPPED_MSG,
                    self.clock(),
                )
            elif off.state == MailOffloadState.RUNNING:
                off.stop_asked = True
            db.commit()
            account_id = off.account_id
        self._changed(account_id, off_id)
```

In `_run`'s batch loop, the `with self.sf() as db:` block that reads `items` also reads the flag. Right after `items = list(...)` inside that block, add `stop = db.get(MailOffload, off_id).stop_asked`, and after `if not items: break` add:

```python
                if stop:
                    raise _Refusing(STOPPED_MSG)
```

In `backend/app/api/mail_offload.py`, add `Response` to the `fastapi` import and add after `get_offload`:

```python
@router.post("/api/mail/offloads/{offload_id}/stop", status_code=204)
def stop_offload(offload_id: int, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        _offloads(request).stop(offload_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return Response(status_code=204)
```

- [ ] **Step 4: Going on after a restart**

In `backend/app/services/mail/offload_plan.py`, replace `INTERRUPTED_OFFLOAD_MSG` and `recover_mail_offloads` with:

```python
RESTARTED_OFFLOAD_MSG = "CleanSync restarted during this offload. It goes on by itself."
STOPPED_AT_RESTART_MSG = "Stopped by you. What wasn't offloaded yet stayed in Gmail."


def recover_mail_offloads(db: Session) -> int:
    """At startup: an offload a restart cut short waits again, and the minute tick starts it.
    A batch Gmail did but CleanSync never recorded is then sent again (bulk offload spec
    §4.4). Its mail job ends INTERRUPTED with the same words, so Activity and the offload's
    page agree (runs before recover_mail_jobs, which would say something else). One the owner
    asked to stop ends stopped. A WAITING offload keeps waiting."""
    rows = list(
        db.scalars(select(MailOffload).where(MailOffload.state == MailOffloadState.RUNNING))
    )
    now = utcnow()
    for off in rows:
        if off.stop_asked:
            off.state, off.reason, off.finished_at = (
                MailOffloadState.FAILED,
                STOPPED_AT_RESTART_MSG,
                now,
            )
        else:
            off.state, off.reason = MailOffloadState.WAITING, RESTARTED_OFFLOAD_MSG
        # One mail job runs at a time per account: its running offload job is this one's.
        for job in db.scalars(
            select(MailJob).where(
                MailJob.account_id == off.account_id,
                MailJob.kind.in_([MailJobKind.OFFLOAD, MailJobKind.DELETE_FOREVER]),
                MailJob.state == JobState.RUNNING,
            )
        ):
            job.state, job.message, job.completed_at = (
                JobState.INTERRUPTED,
                off.reason,
                now,
            )
    db.commit()
    return len(rows)
```

In `backend/app/main.py`, the two comments `# first: it closes its own job as failed` and `# before recover_mail_jobs: it closes its job` become `# first: it closes its own job, so the offload can wait to go on`.

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/test_mail_offload_stop.py tests/test_mail_offload_model.py tests/test_mail_offload_run.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full backend suite and lint**

Run: `cd backend && grep -rn "INTERRUPTED_OFFLOAD_MSG" app tests; uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: the grep finds nothing. Tests and lint PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/mail/offload.py backend/app/services/mail/offload_plan.py backend/app/api/mail_offload.py backend/app/main.py backend/tests/test_mail_offload_stop.py backend/tests/test_mail_offload_model.py
git commit -m "feat(mail): Stop an offload, and go on with it after a restart

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 6: A rule's Offload and Offload on one mail, and the backup's keep-up

**Files:**
- Modify: `backend/app/services/rules/apply.py` (`bus`, `_take_out`, `"removed"`, Undo)
- Modify: `backend/app/main.py` (the Applier gets the bus)
- Modify: `backend/app/services/mail/listing.py` (`decision_chips`: `undoable`, `forever`)
- Create: `backend/tests/test_rules_apply_permanent.py`
- Modify: `backend/tests/test_mail_keep_up.py`

**Interfaces:**
- Consumes: Task 1's `delete`, `PermanentDeleteRefused`; Task 2's `permanent_delete_on`, `permission_lost`.
- Produces:
  - `Applier(..., bus: EventBus | None = None)`; `Applier.bus`
  - `apply.NOT_UNDOABLE_MSG = "It was removed from Gmail for good, so Undo can't bring it back. Use Restore to Gmail in Offloaded."`
  - `apply.is_forever(actions: list[dict] | None) -> bool`
  - An applied Offload's `changes` has `"removed": True` when it went with `messages.delete`.
  - Each chip from `decision_chips` gains `"undoable": bool` and `"forever": bool`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_rules_apply_permanent.py
"""Bulk offload spec §4: a rule's Offload and Offload on one mail use messages.delete when
the mailbox allows permanent delete, messages.trash otherwise."""

from app.models import Account, MailMessage, RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, MailState
from app.services.accounts.oauth import FULL_MAIL_SCOPE
from app.services.accounts.permanent import LOST_MSG
from app.services.mail.listing import decision_chips
from app.services.rules.apply import NOT_UNDOABLE_MSG
from tests.test_rules_apply import decision, get


def allow(gbox):
    with gbox.sf() as db:
        acc = db.get(Account, gbox.acc.id)
        acc.scopes, acc.permanent_delete = FULL_MAIL_SCOPE, True
        db.commit()


async def test_offload_removes_mail_for_good_when_allowed_and_offers_no_undo(gbox):
    allow(gbox)
    row = gbox.add("Promo")
    did = decision(gbox, row, [{"kind": "offload"}])
    assert await gbox.applier.apply(did) == DecisionStatus.APPLIED
    assert row.provider_id not in gbox.fake.messages
    assert get(gbox, RuleDecision, did).changes["removed"] is True
    assert get(gbox, MailMessage, row.id).state == MailState.OFFLOADED
    res = await gbox.applier.undo(did)
    assert res.status == DecisionStatus.APPLIED and res.left == []
    assert get(gbox, RuleDecision, did).reason == NOT_UNDOABLE_MSG
    assert row.provider_id not in gbox.fake.messages


async def test_offload_without_it_still_uses_the_trash_and_can_be_undone(gbox):
    row = gbox.add("Promo")
    did = decision(gbox, row, [{"kind": "offload"}])
    assert await gbox.applier.apply(did) == DecisionStatus.APPLIED
    assert "TRASH" in gbox.fake.messages[row.provider_id].labels
    assert "removed" not in get(gbox, RuleDecision, did).changes
    assert (await gbox.applier.undo(did)).status == DecisionStatus.UNDONE


async def test_a_refused_full_permission_falls_back_to_the_trash(gbox):
    allow(gbox)
    gbox.fake.fail("DELETE", r"/messages/", 403, reason="insufficientPermissions")
    row = gbox.add("Promo")
    did = decision(gbox, row, [{"kind": "offload"}])
    assert await gbox.applier.apply(did) == DecisionStatus.APPLIED
    assert "TRASH" in gbox.fake.messages[row.provider_id].labels
    with gbox.sf() as db:
        acc = db.get(Account, gbox.acc.id)
        assert acc.permanent_delete is False and acc.permanent_delete_note == LOST_MSG


def test_chips_say_when_undo_cant_help_and_when_it_deletes_forever(gbox):
    a, b, c = gbox.add("A"), gbox.add("B"), gbox.add("C")
    with gbox.sf() as db:
        for row, actions, status, changes in (
            (a, [{"kind": "offload"}], DecisionStatus.APPLIED, {"trash": True, "removed": True}),
            (b, [{"kind": "offload"}], DecisionStatus.APPLIED, {"trash": True}),
            (c, [{"kind": "delete_forever"}], DecisionStatus.SUGGESTED, None),
        ):
            db.add(
                RuleDecision(
                    account_id=gbox.acc.id,
                    message_id=row.id,
                    how=DecisionHow.AI,
                    actions=actions,
                    status=status,
                    changes=changes,
                    rule_name="R",
                )
            )
        db.commit()
        chips = decision_chips(db, [a.id, b.id, c.id])
    assert chips[a.id]["undoable"] is False and chips[b.id]["undoable"] is True
    assert chips[c.id]["forever"] is True and chips[a.id]["forever"] is False
```

In `backend/tests/test_mail_keep_up.py`, add (they pin today's rule, spec §9):

```python
async def test_offloaded_mail_removed_from_gmail_for_good_stays_offloaded(box):
    ids = await _backed_up(box, n=1)
    with box.sf() as db:
        row = db.query(MailMessage).filter_by(provider_id=ids[0]).one()
        row.state, row.offload_id = MailState.OFFLOADED, 7
        db.commit()
    box.fake.remove(ids[0])  # a permanent delete: no trash step, straight to messagesDeleted
    await box.run()
    assert _row(box, ids[0]).state == MailState.OFFLOADED and _row(box, ids[0]).offload_id == 7


async def test_a_recheck_leaves_mail_removed_for_good_offloaded(box):
    ids = await _backed_up(box, n=2)
    with box.sf() as db:
        row = db.query(MailMessage).filter_by(provider_id=ids[0]).one()
        row.state, row.offload_id = MailState.OFFLOADED, 7
        db.commit()
    box.fake.forget_history()
    del box.fake.messages[ids[0]]  # gone from Gmail, and no history says so
    box.fake.forget_history()
    state, _, _ = await box.run()
    assert state == JobState.SUCCESS
    assert _row(box, ids[0]).state == MailState.OFFLOADED
    assert eml_path(box.layout, box.acc.email, _row(box, ids[0]).file_path).is_file()
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_rules_apply_permanent.py tests/test_mail_keep_up.py -q`
Expected: the applier and chip tests FAIL (the mail goes to the trash, there's no `NOT_UNDOABLE_MSG`, chips have no `undoable`). The two keep-up tests PASS: they pin what the backup already does.

- [ ] **Step 3: The applier**

In `backend/app/services/rules/apply.py`:
- Change the module docstring's last sentence to `Offload checks the local copy, then removes the mail from Gmail: for good when the mailbox allows permanent delete, else to Gmail's trash (bulk offload spec §4).`
- Add the imports `from app.services.accounts.permanent import permanent_delete_on, permission_lost`, `from app.services.events.bus import EventBus`, and `PermanentDeleteRefused` in the `app.services.mail.adapter` import.
- After `GONE_ROW_MSG`, add:

```python
NOT_UNDOABLE_MSG = (
    "It was removed from Gmail for good, so Undo can't bring it back. "
    "Use Restore to Gmail in Offloaded."
)


def is_forever(actions: list[dict] | None) -> bool:
    return any(isinstance(a, dict) and a.get("kind") == "delete_forever" for a in actions or [])
```

- `Applier.__init__` takes `bus: EventBus | None = None` after `adapter_for` and sets `self.bus = bus or EventBus()`.
- Add the method:

```python
    async def _take_out(self, acc: Account, adapter: MailAdapter, pid: str) -> bool:
        """Offload's Gmail step (bulk offload spec §4): for good when the mailbox allows
        permanent delete, else to Gmail's trash. True when it was removed for good."""
        if permanent_delete_on(acc):
            try:
                await adapter.delete(pid)
                return True
            except PermanentDeleteRefused:
                with self.sf() as db:
                    permission_lost(db, self.bus, acc.id)
        await adapter.trash(pid)
        return False
```

- In `apply`, add `removed = False` just before `adapter = self.adapter_for(acc)`, and replace

```python
            if planned.trash:
                await adapter.trash(pid)
                new_labels = [x for x in labels_now if x != "INBOX"] + ["TRASH"]
```

with

```python
            if planned.trash:
                removed = await self._take_out(acc, adapter, pid)
                new_labels = (
                    labels_now if removed else [x for x in labels_now if x != "INBOX"] + ["TRASH"]
                )
```

- In `apply`'s last `with self.sf() as db:` block, right after `d = db.get(RuleDecision, decision_id)`, add:

```python
            if removed:
                d.changes = {**(d.changes or {}), "removed": True}  # no Undo (spec §4)
```

- In `undo`, right after `seen = list(d.labels_seen or [])`, add:

```python
            if changes.get("removed"):
                d.reason = NOT_UNDOABLE_MSG
                db.commit()
                return UndoResult(DecisionStatus.APPLIED, [])
```

In `backend/app/main.py`, the `Applier(...)` call gains `bus=app.state.bus,`.

- [ ] **Step 4: The chips**

In `backend/app/services/mail/listing.py`, import `is_forever` from `app.services.rules.apply`, and in `decision_chips` add to each chip dict:

```python
            "undoable": not (d.changes or {}).get("removed"),
            "forever": is_forever(d.actions),
```

If importing `app.services.rules.apply` from `listing.py` makes an import cycle, move `is_forever` into `app/services/rules/store.py` instead (beside `action_kind`) and import it from there in both modules.

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/test_rules_apply_permanent.py tests/test_mail_keep_up.py tests/test_rules_apply.py tests/test_mail_offloaded_state.py tests/test_mail_listing.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full backend suite and lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/rules/apply.py backend/app/main.py backend/app/services/mail/listing.py backend/tests/test_rules_apply_permanent.py backend/tests/test_mail_keep_up.py
git commit -m "feat(rules): a rule's Offload removes mail for good when the mailbox allows it

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Delete forever on mail in Gmail

**Files:**
- Modify: `backend/app/services/mail/forever.py` (`mail_record`, `remove_copies`, `unlink_quietly`)
- Modify: `backend/app/services/mail/offload.py` (`start_delete_forever`, `_forever_batch`, the kind in `_run`, `summary`, `_finish`)
- Modify: `backend/app/api/mail_forever.py` (Gmail views start a job; `job_id` in the answer)
- Create: `backend/tests/test_mail_forever_gmail.py`
- Modify: `backend/tests/test_mail_forever.py` (the answer has `job_id`)

**Interfaces:**
- Consumes: Task 4's batch loop, `_batch_call`, `_skip`, `_Refusing`; Task 2's `MailOffloadKind`, `MailJobKind.DELETE_FOREVER`, `allows_permanent_delete`, `permission_lost`, `NOT_ALLOWED_MSG`; round 1's `candidates`, `describe_pick`, `Pick`, `GMAIL_VIEWS`, `MAX_RECORDED`.
- Produces:
  - In `forever.py`: `mail_record(rows: list[MailMessage]) -> list[dict]`, `remove_copies(db, layout, email, rows) -> list[Path]` (not committed), `unlink_quietly(paths: list[Path]) -> None`
  - `MailOffloads.start_delete_forever(account_id: int, pick: Pick, confirm: int) -> tuple[int, int]` (the job's id, and how many picked mails weren't in Gmail)
  - `POST /api/mail/delete-forever` answers `{"deleted": int, "left": int, "job_id": int | None}`.
  - Activity record kind `mail_deleted_forever`, message `"Deleted 3 mails forever from Gmail"`, data `{"mails", "more", "mail_offload_id"}`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mail_forever_gmail.py
"""Delete forever on mail in Gmail (bulk offload spec §5): Gmail first, then CleanSync's
copy, as a background job."""

from sqlalchemy import select

from app.models import Account, Event, MailOffload
from app.models.enums import MailOffloadState, MailState
from app.services.accounts.permanent import NOT_ALLOWED_MSG, permission_lost
from app.services.events.bus import EventBus
from app.services.mail.store import eml_path
from tests.test_mail_bulk_offload import allow_permanent
from tests.test_mail_offload_run import OLD

URL = "/api/mail/delete-forever"


def _forever(auth_client, box, confirm, **body):
    return auth_client.post(URL, json={"account_id": box.acc.id, "confirm": confirm, **body})


async def test_it_removes_the_mail_from_gmail_then_the_copy(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    rows = {s: box.row(s) for s in OLD}
    r = _forever(auth_client, box, 3, view="all", q="older_than:1y")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 0 and body["left"] == 0 and body["job_id"]
    await box.run_queued()
    layout = box.app.state.layout
    for s, row in rows.items():
        assert row.provider_id not in box.fake.messages
        assert box.row(s) is None
        assert not eml_path(layout, box.acc.email, row.file_path).exists()
    d = auth_client.get(f"/api/mail/offloads/{body['job_id']}").json()
    assert d["kind"] == "delete_forever" and d["state"] == "DONE" and d["done"] == 3
    ev = db.scalar(select(Event).where(Event.kind == "mail_deleted_forever"))
    assert ev.message == "Deleted 3 mails forever from Gmail" and len(ev.data["mails"]) == 3
    # Not an offload: not in the Batches tab, and its job has its own kind.
    assert auth_client.get("/api/mail/offloads", params={"account_id": box.acc.id}).json() == []
    jobs = auth_client.get("/api/activity/jobs").json()
    assert jobs[0]["kind"] == "DELETE_FOREVER" and jobs[0]["message"].startswith(
        "Deleted 3 of 3 mails forever"
    )


async def test_a_mail_gmail_wont_delete_keeps_its_copy(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    bad = box.row("Old statement")
    box.fake.fail("POST", r"/batchDelete$", 400)
    box.fake.fail("DELETE", rf"/messages/{bad.provider_id}$", 400)
    job_id = _forever(auth_client, box, 3, view="all", q="older_than:1y").json()["job_id"]
    await box.run_queued()
    kept = box.row("Old statement")
    assert kept is not None and kept.state == MailState.IN_MAIL
    assert eml_path(box.app.state.layout, box.acc.email, kept.file_path).is_file()
    assert bad.provider_id in box.fake.messages
    assert box.row("Old photos") is None and box.row("Ancient note") is None
    d = auth_client.get(f"/api/mail/offloads/{job_id}").json()
    assert d["state"] == "DONE_WITH_SKIPS" and (d["done"], d["skipped"]) == (2, 1)
    assert d["skipped_items"][0]["reason"].startswith("Gmail didn't delete it (")


async def test_it_is_refused_without_permanent_delete(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    r = _forever(auth_client, box, 3, view="all", q="older_than:1y")
    assert r.status_code == 400 and r.json()["detail"] == NOT_ALLOWED_MSG
    assert db.query(MailOffload).count() == 0


async def test_the_typed_count_must_match_and_one_tick_cant_take_the_mailbox(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    r = _forever(auth_client, box, 2, view="all", q="older_than:1y")
    assert r.status_code == 400
    assert r.json()["detail"] == "The number you typed doesn't match the 3 mails picked."
    r = _forever(auth_client, box, 6, view="all", q="")
    assert r.status_code == 400
    assert r.json()["detail"] == "Choose at least one filter, or pick mails in the list."
    assert db.query(MailOffload).count() == 0


async def test_one_picked_recent_mail_has_no_minimum_age(auth_client, offload_box, db):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    row = box.row("Last week")
    r = _forever(auth_client, box, 1, view="inbox", message_ids=[row.id])
    assert r.status_code == 200, r.text
    await box.run_queued()
    assert box.row("Last week") is None and row.provider_id not in box.fake.messages


async def test_a_permission_lost_before_it_runs_fails_it_and_keeps_everything(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    job_id = _forever(auth_client, box, 3, view="all", q="older_than:1y").json()["job_id"]
    permission_lost(db, EventBus(), box.acc.id)
    await box.run_queued()
    off = db.get(MailOffload, job_id)
    db.refresh(off)
    assert off.state == MailOffloadState.FAILED and off.reason.startswith(NOT_ALLOWED_MSG)
    assert "What wasn't deleted yet stays in Gmail" in off.reason
    assert all(box.row(s) is not None for s in OLD)
    assert all(box.row(s).provider_id in box.fake.messages for s in OLD)


async def test_google_refusing_the_permission_mid_job_stops_it_and_turns_it_off(
    auth_client, offload_box, db
):
    box = offload_box
    allow_permanent(box, db)
    await box.backup()
    _forever(auth_client, box, 3, view="all", q="older_than:1y")
    box.fake.fail("POST", r"/batchDelete$", 403, reason="insufficientPermissions")
    await box.run_queued()
    off = db.query(MailOffload).one()
    assert off.state == MailOffloadState.FAILED and off.done == 0
    assert all(box.row(s) is not None for s in OLD)
    acc = db.get(Account, box.acc.id)
    db.refresh(acc)
    assert acc.permanent_delete is False
```

In `backend/tests/test_mail_forever.py` (round 1), in `test_the_endpoint`, `assert auth_client.post(url, json=body).json() == {"deleted": 1, "left": 0}` becomes `assert auth_client.post(url, json=body).json() == {"deleted": 1, "left": 0, "job_id": None}`. `test_only_the_cleansync_views_take_it` stays as it is: it calls the service function, which still takes only Offloaded and Deleted in Gmail; the endpoint sends Gmail views to the job.

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_mail_forever_gmail.py tests/test_mail_forever.py -q`
Expected: FAIL. The endpoint refuses Gmail views with "Delete forever works in Offloaded and Deleted in Gmail." and has no `job_id`.

- [ ] **Step 3: Shared copy removal in `forever.py`**

In `backend/app/services/mail/forever.py`, add `from pathlib import Path` and these functions after `_mails`:

```python
def mail_record(rows: list[MailMessage]) -> list[dict]:
    """What Activity keeps of mail deleted forever: sender, subject and date, at most 500."""
    return [
        {"from": r.from_addr, "subject": r.subject, "date": r.date.isoformat()}
        for r in rows[:MAX_RECORDED]
    ]


def remove_copies(db: Session, layout: Layout, email: str, rows: list[MailMessage]) -> list[Path]:
    """Removes the rows and their search entries, not committed. Their decisions, to-do and
    offload items go with them (ON DELETE CASCADE). Returns the files, to unlink only after
    the commit: a leftover file is harmless, a row without one isn't."""
    paths = [eml_path(layout, email, r.file_path) for r in rows]
    delete_search_rows(db, [r.id for r in rows], commit=False)
    for r in rows:
        db.delete(r)
    return paths


def unlink_quietly(paths: list[Path]) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError as e:
            log.warning("could not remove %s: %s", p.name, e)
```

In `delete_forever`, replace from `record = [` to the end of the function with:

```python
    record = mail_record(rows)
    paths = remove_copies(db, layout, email, rows)  # one commit: rows, search, record
    record_event(
        db,
        bus,
        kind="mail_deleted_forever",
        message=f"Deleted {_mails(len(rows))} forever from {name}",
        account_id=account_id,
        data={"mails": record, "more": max(0, len(rows) - MAX_RECORDED)},
    )  # commits
    unlink_quietly(paths)
    return ForeverResult(len(rows), left)
```

- [ ] **Step 4: The job in `offload.py`**

In `backend/app/services/mail/offload.py`, add to the imports: `NOT_ALLOWED_MSG` in the `app.services.accounts.permanent` import; `candidates`, `describe_pick` in the `app.services.mail.offload_plan` import; and

```python
from app.services.mail.forever import (
    MAX_RECORDED,
    mail_record,
    remove_copies,
    unlink_quietly,
)
```

After `STOPPED_MSG`, add:

```python
FOREVER = MailOffloadKind.DELETE_FOREVER
FOREVER_REST = "What wasn't deleted yet stays in Gmail, with CleanSync's copy."
FOREVER_NOT_ALLOWED_MSG = f"{NOT_ALLOWED_MSG} {REST_STAYED}"
SKIP_NOT_DELETED_KEPT = "Gmail didn't delete it ({error}), so it stayed in Gmail. The copy here is kept."
NONE_IN_GMAIL_MSG = "None of these mails are in Gmail."
```

Replace `summary` with:

```python
def summary(off: MailOffload) -> str:
    if off.kind == FOREVER:
        text = f"Deleted {off.done:,} of {off.total:,} mails forever"
    else:
        text = f"Offloaded {off.done:,} of {off.total:,} mails ({human_size(off.bytes)})"
    return text + (f"; {off.skipped:,} skipped" if off.skipped else "")
```

Add to `MailOffloads`, after `start`:

```python
    async def start_delete_forever(self, account_id: int, pick: Pick, confirm: int) -> tuple[int, int]:
        """Bulk offload spec §5: Delete forever on mail in Gmail, as a background job. Only
        for a mailbox that allows permanent delete; the typed count must match. No minimum
        age. Returns the job's id, and how many picked mails were left (not in Gmail)."""
        await self._ready(account_id)  # the disk, the login, Mail set up, no full listing
        with self.sf() as db:
            if not allows_permanent_delete(db, account_id):
                raise MailError(NOT_ALLOWED_MSG, 400)
            try:
                rows = candidates(db, account_id, pick, self.clock())
            except SearchError as e:
                raise MailError(str(e), 400) from e
            mine = sorted(
                (r for r in rows if r.state == MailState.IN_MAIL),
                key=lambda r: (as_utc(r.date), r.id),
            )
            left = len(rows) - len(mine) if (pick.message_ids or pick.thread_ids) else 0
            if not mine:
                raise MailError(NONE_IN_GMAIL_MSG, 400)
            if confirm != len(mine):
                raise MailError(
                    f"The number you typed doesn't match the {_mails(len(mine))} picked.", 400
                )
            name = describe_pick(db, account_id, pick, len(mine))
            off = MailOffload(
                account_id=account_id,
                kind=FOREVER,
                filter_text=name,
                state=MailOffloadState.WAITING,
                total=len(mine),
            )
            db.add(off)
            db.flush()
            db.add_all(MailOffloadItem(offload_id=off.id, message_id=r.id) for r in mine)
            db.commit()
            off_id = off.id
            record_event(
                db,
                self.bus,
                kind="mail_delete_forever_started",
                message=f"Delete forever of {_mails(len(mine))} asked for ({name})",
                account_id=account_id,
                data={"mail_offload_id": off_id},
            )
        # The same queue action as an offload: it waits behind the mailbox's other mail jobs.
        self.mail.queue.request(account_id, "offload", {"offload_id": off_id}, trigger="MANUAL")
        self._changed(account_id, off_id)
        return off_id, left
```

Add after `_offload_batch`:

```python
    def _forget(self, acc: Account, off_id: int, gone: list[tuple[int, int, str]]) -> None:
        """Gmail removed these: CleanSync's copies go too, with one Activity record."""
        if not gone:
            return
        with self.sf() as db:
            rows = [r for _, mid, _ in gone if (r := db.get(MailMessage, mid)) is not None]
            off = db.get(MailOffload, off_id)
            off.done += len(rows)
            off.removed += len(rows)
            off.bytes += sum(r.size for r in rows)
            record = mail_record(rows)
            paths = remove_copies(db, self.layout, acc.email, rows)  # their items go too
            record_event(
                db,
                self.bus,
                kind="mail_deleted_forever",
                message=f"Deleted {_mails(len(rows))} forever from Gmail",
                account_id=acc.id,
                data={
                    "mails": record,
                    "more": max(0, len(rows) - MAX_RECORDED),
                    "mail_offload_id": off_id,
                },
            )  # commits
        unlink_quietly(paths)

    def _lost_forever(self, account_id: int) -> None:
        self._permission_lost(account_id)
        raise _Refusing(FOREVER_NOT_ALLOWED_MSG)

    async def _forever_batch(
        self, acc: Account, adapter, off_id: int, item_ids: list[int], log_path
    ) -> None:
        """Bulk offload spec §5: Gmail first, in one call; then CleanSync's copy of each mail
        Gmail removed. A mail Gmail didn't remove keeps its copy and is listed."""
        if not self._permanent(acc.id):
            raise _Refusing(FOREVER_NOT_ALLOWED_MSG)
        ready: list[tuple[int, int, str]] = []
        left: list[int] = []
        with self.sf() as db:
            for item_id in item_ids:
                row = db.get(MailMessage, db.get(MailOffloadItem, item_id).message_id)
                # DELETED: sent before a pause or a restart and not recorded; sent again.
                if row.state in (MailState.IN_MAIL, MailState.DELETED):
                    ready.append((item_id, row.id, row.provider_id))
                else:
                    left.append(item_id)
        for item_id in left:
            self._skip(off_id, item_id, SKIP_NOT_IN_GMAIL, log_path)
        if not ready:
            return
        try:
            await self._batch_call(adapter, [pid for _, _, pid in ready], True)
            gone = ready
        except PermanentDeleteRefused:
            return self._lost_forever(acc.id)
        except (MailAuthError, MailSetupProblem, MailUnreachable):
            raise
        except MailApiError as e:
            job_log(log_path, f"Gmail refused the batch ({e}); one mail at a time instead")
            gone = await self._forever_one_by_one(acc, adapter, off_id, ready, log_path)
        self._forget(acc, off_id, gone)
        job_log(log_path, f"deleted {len(gone)} mails forever")

    async def _forever_one_by_one(
        self, acc: Account, adapter, off_id: int, ready: list[tuple[int, int, str]], log_path
    ) -> list[tuple[int, int, str]]:
        gone, refused = [], []
        for item_id, msg_id, pid in ready:
            try:
                await adapter.delete(pid)
            except MailNotFound:
                pass  # Gmail no longer has it: only the copy is left to remove
            except PermanentDeleteRefused:
                self._forget(acc, off_id, gone)  # what Gmail already removed
                return self._lost_forever(acc.id)
            except (MailAuthError, MailSetupProblem, MailUnreachable):
                self._forget(acc, off_id, gone)
                raise
            except MailApiError as e:
                self._skip(off_id, item_id, SKIP_NOT_DELETED_KEPT.format(error=e), log_path)
                refused.append(str(e))
                if len(refused) >= REFUSED_IN_A_ROW:
                    self._forget(acc, off_id, gone)
                    raise _Refusing(OFFLOAD_REFUSED_MSG.format(n=len(refused), error=e)) from e
                continue
            refused = []
            gone.append((item_id, msg_id, pid))
        return gone
```

In `_run`:
- In the first `with self.sf() as db:` block, change `email = acc.email` to `email, kind = acc.email, off.kind`.
- In the `start_mail_job(...)` call, `MailJobKind.OFFLOAD` becomes `MailJobKind.DELETE_FOREVER if kind == FOREVER else MailJobKind.OFFLOAD`.
- Right after `adapter = self.mail.adapter_for(acc)`, add `do_batch = self._forever_batch if kind == FOREVER else self._offload_batch`, and `await self._offload_batch(acc, adapter, off_id, items, job.log_path)` becomes `await do_batch(acc, adapter, off_id, items, job.log_path)`.

In `_finish`, right after `off = db.get(MailOffload, off_id)`, add:

```python
            if off.kind == FOREVER and reason:
                reason = reason.replace(REST_STAYED, FOREVER_REST)
```

and its event message `f"Mail offload for {email}: {message}"` becomes `f"{'Delete forever' if off.kind == FOREVER else 'Mail offload'} for {email}: {message}"`. In `stop`, the waiting branch's `STOPPED_MSG` becomes `STOPPED_MSG.replace(REST_STAYED, FOREVER_REST) if off.kind == FOREVER else STOPPED_MSG`.

- [ ] **Step 5: The endpoint**

Replace `backend/app/api/mail_forever.py`'s route with:

```python
@router.post("/delete-forever")
async def forever(
    body: ForeverIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    """In Offloaded and Deleted in Gmail: removes CleanSync's copy now (mail panel spec §7).
    In a Gmail view: starts a background job that removes the mail from Gmail, then the copy
    (bulk offload spec §5); job_id names it (GET /api/mail/offloads/{id})."""
    acc = account_or_404(db, body.account_id)
    if body.view in GMAIL_VIEWS and body.view != "suggested":
        pick = Pick(
            q=body.q,
            message_ids=body.message_ids,
            view=body.view,
            label=body.label or None,
        )
        try:
            job_id, left = await request.app.state.mail_offloads.start_delete_forever(
                acc.id, pick, body.confirm
            )
        except MailError as e:
            raise HTTPException(e.status, str(e)) from e
        return {"deleted": 0, "left": left, "job_id": job_id}
    queue = request.app.state.mail.queue
    try:
        res = delete_forever(
            db,
            request.app.state.layout,
            request.app.state.bus,
            acc.id,
            acc.email,
            view=body.view,
            label=body.label or None,
            q=body.q,
            message_ids=body.message_ids,
            confirm=body.confirm,
            now=utcnow(),
            restore_queued=lambda aid: any(a == "restore" for a, _ in queue.waiting(aid)),
        )
    except ForeverProblem as e:
        raise HTTPException(400, str(e)) from e
    request.app.state.bus.publish("mail", {"account_id": acc.id})
    return {"deleted": res.deleted, "left": res.left, "job_id": None}
```

with the imports `from app.services.mail.offload_plan import Pick`, `from app.services.mail.search import GMAIL_VIEWS` and `from app.services.mail.service import MailError`. Change the module docstring to `"""Delete forever: CleanSync's copy in Offloaded and Deleted in Gmail (mail panel spec §7), and mail in Gmail as a job (bulk offload spec §5)."""`.

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest tests/test_mail_forever_gmail.py tests/test_mail_forever.py tests/test_mail_bulk_offload.py tests/test_mail_offload_stop.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full backend suite and lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/mail/forever.py backend/app/services/mail/offload.py backend/app/api/mail_forever.py backend/tests/test_mail_forever_gmail.py backend/tests/test_mail_forever.py
git commit -m "feat(mail): Delete forever on mail in Gmail, as a background job

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 8: Delete forever as a rule action

**Files:**
- Modify: `backend/app/services/rules/store.py` (the action is allowed, stands alone, Suggest first, needs the permission when added)
- Modify: `backend/app/services/rules/apply.py` (`_apply_forever`, `_not_allowed`)
- Modify: `backend/app/services/mail/forever.py` (`record_rule_forever`)
- Modify: `backend/app/services/rules/decisions.py` (`approve(..., confirm)`)
- Modify: `backend/app/services/rules/jobs.py` (a decision that left with its mail)
- Modify: `backend/app/schemas/rules.py` (`ApproveBody`, `RuleOut.blocked`)
- Modify: `backend/app/api/rules.py` (`approve` takes `confirm`; `_rule_out` sets `blocked`)
- Create: `backend/tests/test_rules_forever.py`
- Modify: `backend/tests/test_rules_store.py` (round 1's "not yet allowed" test goes)

**Interfaces:**
- Consumes: Task 6's `is_forever`, `Applier.bus`; Task 7's `mail_record`, `remove_copies`, `unlink_quietly`; Task 2's `NOT_ALLOWED_MSG`, `allows_permanent_delete`, `permanent_delete_on`, `permission_lost`; round 1's `store.ACTION_KINDS`, `STANDS_ALONE`, `NOT_YET_KINDS`; round 2's `RuleJobs._withdraw_suggestions` and `jobs.HEAVY_KINDS`.
- Produces:
  - `store.ACTION_KINDS = ("archive", "label", "mark_read", "star", "offload", "delete_forever")`; `store.NOT_YET_KINDS` is gone.
  - `store.STANDS_ALONE = {"offload": "Offload", "delete_forever": "Delete forever"}`, `store.SUGGEST_ONLY_MSG`
  - `forever.record_rule_forever(db, bus, account_id: int, rule_name: str, mails: list[dict], now: datetime) -> None` (commits)
  - `Decisions.approve(account_id: int, ids: list[int], confirm: int | None = None) -> dict[str, int]`; `decisions.FOREVER_CONFIRM_MSG`
  - `POST /api/rule-decisions/approve` takes `{account_id, ids, confirm?}`.
  - `GET /api/rules` rows gain `blocked: str | null`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_rules_forever.py
"""Delete forever as a rule action (bulk offload spec §6)."""

import pytest
from sqlalchemy import select

from app.models import Account, Event, MailMessage, RuleDecision
from app.models.enums import DecisionStatus
from app.services.accounts.oauth import FULL_MAIL_SCOPE
from app.services.accounts.permanent import NOT_ALLOWED_MSG, turn_off
from app.services.mail.store import eml_path
from app.services.rules.decisions import DecisionProblem, Decisions
from app.services.rules.jobs import RuleJobs
from app.services.rules.store import RuleIn, RuleProblem, create_rule, update_rule
from tests.test_rules_apply import decision, get
from tests.test_rules_apply_permanent import allow

FOREVER = [{"kind": "delete_forever"}]


def _allowed(db, make_account):
    acc = make_account()
    acc.scopes, acc.permanent_delete = FULL_MAIL_SCOPE, True
    db.commit()
    return acc


def _rule(**kw):
    return RuleIn(name="Junk", description="junk mail", actions=FOREVER, **kw)


def test_it_cant_be_saved_without_permanent_delete(db, make_account):
    acc = make_account()
    with pytest.raises(RuleProblem, match=r"^Turn on Allow permanent delete in Settings → Accounts\.$"):
        create_rule(db, acc.id, _rule(), "admin")


def test_it_always_suggests_first_and_stands_alone(db, make_account):
    acc = _allowed(db, make_account)
    with pytest.raises(RuleProblem, match="always suggests first"):
        create_rule(db, acc.id, _rule(mode="automatic"), "admin")
    both = RuleIn(name="x", description="d", actions=[*FOREVER, {"kind": "star"}])
    with pytest.raises(RuleProblem, match=r"^Delete forever can't be combined with other actions\.$"):
        create_rule(db, acc.id, both, "admin")
    assert create_rule(db, acc.id, _rule(), "admin").actions == FOREVER


def test_a_rule_that_has_it_can_still_be_edited_once_the_permission_is_off(db, make_account):
    acc = _allowed(db, make_account)
    rule = create_rule(db, acc.id, _rule(), "admin")
    turn_off(db, acc.id)
    changed = update_rule(db, acc.id, rule.id, _rule(enabled=False), "admin")
    assert changed.enabled is False and changed.actions == FOREVER


async def test_on_approval_gmail_goes_first_then_the_copy_with_one_record_per_rule(gbox):
    allow(gbox)
    rows = [gbox.add("Junk 1"), gbox.add("Junk 2")]
    for row in rows:
        did = decision(gbox, row, FOREVER)
        assert await gbox.applier.apply(did) == DecisionStatus.APPLIED
        assert row.provider_id not in gbox.fake.messages
        assert get(gbox, MailMessage, row.id) is None
        assert get(gbox, RuleDecision, did) is None  # its rule history goes with it
        assert not eml_path(gbox.layout, gbox.acc.email, row.file_path).exists()
    with gbox.sf() as db:
        events = db.scalars(select(Event).where(Event.kind == "rule_deleted_forever")).all()
        assert [e.message for e in events] == ["Rule R deleted 2 mails forever"]
        assert [m["subject"] for m in events[0].data["mails"]] == ["Junk 1", "Junk 2"]


async def test_it_waits_with_its_reason_when_permanent_delete_is_off(gbox):
    row = gbox.add("Junk")
    did = decision(gbox, row, FOREVER)
    assert await gbox.applier.apply(did) == DecisionStatus.SUGGESTED
    assert get(gbox, RuleDecision, did).reason == NOT_ALLOWED_MSG
    assert row.provider_id in gbox.fake.messages and get(gbox, MailMessage, row.id) is not None
    planned = decision(gbox, gbox.add("Junk 2"), FOREVER, status=DecisionStatus.PLANNED)
    assert await gbox.applier.apply(planned) == DecisionStatus.FAILED  # a run moves on
    assert get(gbox, RuleDecision, planned).reason == NOT_ALLOWED_MSG


async def test_google_refusing_it_turns_the_switch_off_and_keeps_the_mail(gbox):
    allow(gbox)
    gbox.fake.fail("DELETE", r"/messages/", 403, reason="insufficientPermissions")
    row = gbox.add("Junk")
    did = decision(gbox, row, FOREVER)
    assert await gbox.applier.apply(did) == DecisionStatus.SUGGESTED
    assert get(gbox, RuleDecision, did).reason == NOT_ALLOWED_MSG
    assert get(gbox, MailMessage, row.id) is not None and row.provider_id in gbox.fake.messages
    assert get(gbox, Account, gbox.acc.id).permanent_delete is False


async def test_a_mail_gmail_no_longer_has_still_loses_its_copy(gbox):
    allow(gbox)
    row = gbox.add("Junk")
    gbox.fake.remove(row.provider_id)
    did = decision(gbox, row, FOREVER)
    assert await gbox.applier.apply(did) == DecisionStatus.APPLIED
    assert get(gbox, MailMessage, row.id) is None


async def test_approving_it_in_suggested_needs_the_typed_count(gbox):
    allow(gbox)
    rows = [gbox.add("Junk 1"), gbox.add("Junk 2")]
    ids = [decision(gbox, r, FOREVER) for r in rows]
    decisions = Decisions(gbox.sf, gbox.applier)
    with pytest.raises(DecisionProblem, match=r"^Type 2 to delete these mails forever\.$"):
        await decisions.approve(gbox.acc.id, ids)
    with pytest.raises(DecisionProblem):
        await decisions.approve(gbox.acc.id, ids, confirm=1)
    assert all(r.provider_id in gbox.fake.messages for r in rows)
    assert await decisions.approve(gbox.acc.id, ids, confirm=2) == {"applied": 2}


def test_a_run_survives_a_decision_that_left_with_its_mail(gbox):
    jobs = RuleJobs(gbox.sf, gbox.applier, None)
    with gbox.sf() as db:
        jobs._withdraw_suggestions(db, 999_999)  # deleted forever: nothing to withdraw


def test_the_rule_card_says_why_a_delete_forever_rule_waits(auth_client, db, make_account):
    acc = _allowed(db, make_account)
    create_rule(db, acc.id, _rule(), "admin")
    rules = auth_client.get("/api/rules", params={"account_id": acc.id}).json()
    assert rules[0]["blocked"] is None
    turn_off(db, acc.id)
    rules = auth_client.get("/api/rules", params={"account_id": acc.id}).json()
    assert rules[0]["blocked"] == NOT_ALLOWED_MSG


def test_the_approve_endpoint_passes_the_typed_count(auth_client, db, make_account):
    acc = make_account()
    r = auth_client.post(
        "/api/rule-decisions/approve", json={"account_id": acc.id, "ids": [], "confirm": 0}
    )
    assert r.status_code == 200 and r.json() == {}
```

In `backend/tests/test_rules_store.py`, delete round 1's `test_delete_forever_is_listed_but_not_yet_allowed` (the action works now; `test_it_cant_be_saved_without_permanent_delete` replaces it).

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_rules_forever.py -q`
Expected: FAIL. Saving says "Delete forever needs full Gmail access (coming in this release).", the applier plans a label change, and `approve` takes no `confirm`.

- [ ] **Step 3: The rule store**

In `backend/app/services/rules/store.py`:
- Import `is_forever` beside `is_system_label` from `app.services.rules.apply`, and `NOT_ALLOWED_MSG, allows_permanent_delete` from `app.services.accounts.permanent`.
- Replace `ACTION_KINDS`, `NOT_YET_KINDS` and `STANDS_ALONE` with:

```python
ACTION_KINDS = ("archive", "label", "mark_read", "star", "offload", "delete_forever")
# These each make the whole rule: nothing else can happen to the mail as well.
STANDS_ALONE = {"offload": "Offload", "delete_forever": "Delete forever"}
SUGGEST_ONLY_MSG = "A Delete forever rule always suggests first. Pick Suggest first."
```

- In `clean_actions`, delete the two lines that check `NOT_YET_KINDS`, and replace the stands-alone check with:

```python
    for kind, word in STANDS_ALONE.items():
        if any(a["kind"] == kind for a in out) and len(out) > 1:
            raise RuleProblem(f"{word} can't be combined with other actions.")
```

- In `_clean`, compute `actions = clean_actions(data.actions)` before the `return`, add

```python
    if is_forever(actions) and data.mode == RuleMode.AUTOMATIC:
        raise RuleProblem(SUGGEST_ONLY_MSG)  # bulk offload spec §6
```

  and use `"actions": actions,` in the returned dict.
- Add:

```python
def _check_forever(db: Session, account_id: int, actions: list[dict], had: bool) -> None:
    """Adding Delete forever needs permanent delete on for the mailbox. A rule that already
    has it can still be edited or turned off after the permission is gone."""
    if is_forever(actions) and not had and not allows_permanent_delete(db, account_id):
        raise RuleProblem(NOT_ALLOWED_MSG)
```

- In `create_rule`, right after `fields = _clean(data)`, add `_check_forever(db, account_id, fields["actions"], had=False)`. In `update_rule`, right after `fields = _clean(data)`, add `_check_forever(db, account_id, fields["actions"], had=is_forever(rule.actions))`.

- [ ] **Step 4: The grouped Activity record**

In `backend/app/services/mail/forever.py`, add `from datetime import timedelta`, `Event` to the `app.models` import, `as_utc` from `app.core.clock`, and `event_to_dict` beside `record_event`. Add:

```python
RULE_GROUP = timedelta(minutes=10)


def record_rule_forever(
    db: Session, bus: EventBus, account_id: int, rule_name: str, mails: list[dict], now: datetime
) -> None:
    """One Activity record per rule (plan decision 15): mail its Delete forever removed within
    10 minutes of the rule's last record joins that record. Commits."""
    last = db.scalar(
        select(Event)
        .where(Event.kind == "rule_deleted_forever", Event.account_id == account_id)
        .order_by(Event.id.desc())
        .limit(1)
    )
    data = dict(last.data or {}) if last is not None else {}
    if last is not None and data.get("rule") == rule_name and as_utc(last.ts) >= now - RULE_GROUP:
        kept = list(data.get("mails") or [])
        room = max(0, MAX_RECORDED - len(kept))
        count = int(data.get("count") or 0) + len(mails)
        last.data = {
            **data,
            "count": count,
            "mails": kept + mails[:room],
            "more": int(data.get("more") or 0) + max(0, len(mails) - room),
        }
        last.message = f"Rule {rule_name} deleted {_mails(count)} forever"
        db.commit()
        bus.publish("activity", event_to_dict(last))
        return
    record_event(
        db,
        bus,
        kind="rule_deleted_forever",
        message=f"Rule {rule_name} deleted {_mails(len(mails))} forever",
        account_id=account_id,
        data={
            "rule": rule_name,
            "count": len(mails),
            "mails": mails[:MAX_RECORDED],
            "more": max(0, len(mails) - MAX_RECORDED),
        },
    )
```

- [ ] **Step 5: The applier**

In `backend/app/services/rules/apply.py`, import `NOT_ALLOWED_MSG` beside `permanent_delete_on` from `app.services.accounts.permanent`, and

```python
from app.services.mail.forever import (
    mail_record,
    record_rule_forever,
    remove_copies,
    unlink_quietly,
)
```

In `apply`, right after `if state != MailState.IN_MAIL: return self._fail(decision_id, NOT_IN_MAIL_MSG)`, add:

```python
        if is_forever(actions):
            return await self._apply_forever(decision_id, acc)
```

Add the methods:

```python
    def _not_allowed(self, decision_id: int) -> str:
        """Permanent delete is off: a suggestion waits with the reason; a planned change of a
        run fails with it, so the run never comes back to it (plan decision 18)."""
        with self.sf() as db:
            d = db.get(RuleDecision, decision_id)
            d.reason = NOT_ALLOWED_MSG
            if d.status == DecisionStatus.PLANNED:
                d.status = DecisionStatus.FAILED
            db.commit()
            return d.status

    async def _apply_forever(self, decision_id: int, acc: Account) -> str:
        """Delete forever (bulk offload spec §6, §5): Gmail first, then CleanSync's copy. The
        decision goes with the mail (its rule history), so Activity keeps the record."""
        if not permanent_delete_on(acc):
            return self._not_allowed(decision_id)
        with self.sf() as db:
            d = db.get(RuleDecision, decision_id)
            msg_id, rule_name = d.message_id, d.rule_name or "A rule"
            pid = db.get(MailMessage, msg_id).provider_id
        adapter = self.adapter_for(acc)
        try:
            await adapter.delete(pid)
        except PermanentDeleteRefused:
            with self.sf() as db:
                permission_lost(db, self.bus, acc.id)
            return self._not_allowed(decision_id)
        except WAIT_ERRORS as e:
            with self.sf() as db:
                db.get(RuleDecision, decision_id).reason = _wait_reason(e)
                db.commit()
            raise
        except MailNotFound:
            pass  # already gone from Gmail: only the copy is left to remove
        except MailApiError as e:
            return self._fail(decision_id, str(e))
        with self.sf() as db:
            row = db.get(MailMessage, msg_id)
            if row is not None:
                record = mail_record([row])
                paths = remove_copies(db, self.layout, acc.email, [row])
                record_rule_forever(db, self.bus, acc.id, rule_name, record, self.clock())
                unlink_quietly(paths)  # after the commit
        return DecisionStatus.APPLIED
```

- [ ] **Step 6: Approving in Suggested, and runs**

In `backend/app/services/rules/decisions.py`, import `is_forever` beside `WAIT_ERRORS` from `app.services.rules.apply`. Add after `DecisionProblem`:

```python
FOREVER_CONFIRM_MSG = "Type {n} to delete these mails forever."
```

Replace `approve` with:

```python
    async def approve(
        self, account_id: int, ids: list[int], confirm: int | None = None
    ) -> dict[str, int]:
        """Delete forever suggestions need their count typed (plan decision 16); the sender
        is read first because such a mail is gone once applied."""
        with self.sf() as db:
            todo = []
            for d in self._mine(db, account_id, ids):
                if d.status != DecisionStatus.SUGGESTED:
                    continue
                row = db.get(MailMessage, d.message_id)
                sender = sender_of(row.from_addr) if row is not None else ""
                todo.append((d.id, d.rule_id, sender, is_forever(d.actions)))
        forever = sum(1 for t in todo if t[3])
        if forever and confirm != forever:
            raise DecisionProblem(FOREVER_CONFIRM_MSG.format(n=f"{forever:,}"))
        counts: Counter = Counter()
        for did, rule_id, sender, _ in todo:
            try:
                status = await self.applier.apply(did)
            except WAIT_ERRORS:
                counts["waiting"] += 1
                continue
            counts[status] += 1
            if status == DecisionStatus.APPLIED and rule_id is not None and sender:
                with self.sf() as db:
                    learn(db, account_id, rule_id, sender, self.clock())
                    db.commit()
        return dict(counts)
```

In `backend/app/services/rules/jobs.py`, at the top of `_withdraw_suggestions`, right after `d = db.get(RuleDecision, decision_id)`, add:

```python
        if d is None:
            return  # deleted forever: the decision went with its mail
```

Round 2's run approval already asks for the typed count of picked mails whose rule offloads or deletes forever (`jobs.HEAVY_KINDS = ("offload", "delete_forever")`, round 2's plan decision 12), and its run loop still calls `self.applier.apply(did)` and then `_withdraw_suggestions`. So the guard above is the only change a run needs: `_apply_forever` returns `applied`, the run counts it, and the decision is already gone.

In `backend/app/schemas/rules.py`, add:

```python
class ApproveBody(IdsBody):
    confirm: int | None = None  # the count of Delete forever suggestions, typed
```

and add `blocked: str | None = None` to `RuleOut` (after `senders`).

In `backend/app/api/rules.py`:
- Import `ApproveBody` from `app.schemas.rules`, `is_forever` from `app.services.rules.apply`, and `NOT_ALLOWED_MSG, allows_permanent_delete` from `app.services.accounts.permanent`.
- `approve` takes `body: ApproveBody` and its call becomes:

```python
    try:
        out = await request.app.state.rule_decisions.approve(
            body.account_id, body.ids, body.confirm
        )
    except PROBLEMS as e:
        raise _problem(e) from e
```

- In `_rule_out`, the `RuleOut(...)` call gains:

```python
        blocked=(
            NOT_ALLOWED_MSG
            if is_forever(rule.actions) and not allows_permanent_delete(db, rule.account_id)
            else None
        ),
```

- [ ] **Step 7: Run the tests**

Run: `cd backend && uv run pytest tests/test_rules_forever.py tests/test_rules_store.py tests/test_rules_apply.py tests/test_rules_decisions.py tests/test_rules_jobs.py tests/test_rules_api.py -q`
Expected: PASS.

- [ ] **Step 8: Run the full backend suite and lint**

Run: `cd backend && grep -rn "NOT_YET_KINDS\|coming in this release" app tests; uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: the grep finds nothing. Tests and lint PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/rules backend/app/services/mail/forever.py backend/app/schemas/rules.py backend/app/api/rules.py backend/tests/test_rules_forever.py backend/tests/test_rules_store.py
git commit -m "feat(rules): Delete forever works as a rule action for mailboxes that allow it

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 9: Settings → Accounts: Allow permanent delete

**Files:**
- Modify: `frontend/src/api/types.ts` (`Account.permanent_delete`, `permanent_delete_note`)
- Modify: `frontend/src/test/utils.tsx` (`ACCOUNT` gains the two fields)
- Create: `frontend/src/components/settings/PermanentDelete.tsx`
- Modify: `frontend/src/pages/SettingsPage.tsx` (`AccountRow` shows it)
- Modify: `frontend/src/routes.tsx` (`OAuthReturn` toasts for `permanent=`)
- Modify: `frontend/src/pages/Settings.test.tsx`

**Interfaces:**
- Consumes: Task 3's `POST /api/accounts/{id}/permanent-delete` and the accounts list's `permanent_delete`, `permanent_delete_note`; the callback's `permanent=on|denied`.
- Produces:
  - `Account.permanent_delete: boolean`, `Account.permanent_delete_note: string | null`
  - `PermanentDelete({ account }: { account: Account })`, a `role="group"` named "Allow permanent delete"
  - `PERMANENT_OFF_TEXT`, `PERMANENT_ON_TEXT`, `PERMANENT_REVOKE_TEXT`, `PERMANENT_ON_TOAST`, `PERMANENT_DENIED_TOAST` exported from `PermanentDelete.tsx`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/pages/Settings.test.tsx`, add inside `describe("settings", …)`:

```tsx
  it("turns Allow permanent delete on through Google's sign-in", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const { calls } = mockApi({
      "GET /api/accounts": [{ ...ACCOUNT, services: { files: "active", mail: "active" } }],
      "POST /api/accounts/1/permanent-delete": { auth_url: "https://accounts.google.com/full" },
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=accounts", path: "/settings" });
    const box = await screen.findByRole("group", { name: "Allow permanent delete" });
    expect(box).toHaveTextContent("Off");
    expect(box).toHaveTextContent(
      "Offload moves mail to Gmail's trash. Turn this on to delete it from Gmail directly, so the space comes back at once.",
    );
    await userEvent.click(within(box).getByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/full"));
    expect(calls.find((c) => c.path === "/api/accounts/1/permanent-delete")?.body).toEqual({ on: true });
  });

  it("says what On means, how to take it back at Google, and turns it off", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [{ ...ACCOUNT, services: { files: "active", mail: "active" }, permanent_delete: true }],
      "POST /api/accounts/1/permanent-delete": { auth_url: null },
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=accounts", path: "/settings" });
    const box = await screen.findByRole("group", { name: "Allow permanent delete" });
    expect(box).toHaveTextContent("On");
    expect(box).toHaveTextContent("Offload and Delete forever remove mail from Gmail directly. Gmail's trash isn't used.");
    expect(box).toHaveTextContent("myaccount.google.com/permissions");
    await userEvent.click(within(box).getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/accounts/1/permanent-delete")?.body).toEqual({ on: false }));
  });

  it("shows why permanent delete went off, and nothing for a mailbox without mail backup", async () => {
    mockApi({
      "GET /api/accounts": [
        {
          ...ACCOUNT,
          services: { files: "active", mail: "active" },
          permanent_delete_note: "Permanent delete was turned off: Google no longer allows it for this mailbox.",
        },
        { ...ACCOUNT, id: 2, email: "bob@example.com" },
      ],
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=accounts", path: "/settings" });
    expect(await screen.findByText("Permanent delete was turned off: Google no longer allows it for this mailbox.")).toBeInTheDocument();
    expect(screen.getAllByRole("group", { name: "Allow permanent delete" })).toHaveLength(1);
  });

  it("says what Google answered after the sign-in for permanent delete", async () => {
    sessionStorage.setItem("drivesync.oauthReturn", "/settings?tab=accounts");
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "CleanSync" },
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 1, pairs: 1, initialized_pairs: 1 },
    });
    renderApp(<AppRoutes />, { route: "/?connected=1&permanent=denied" });
    expect(await screen.findByText("Google didn't give full Gmail access, so permanent delete stays off. Try again and tick the box for it.")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd frontend && npm test -- --run src/pages/Settings.test.tsx`
Expected: FAIL: there is no "Allow permanent delete" group, and the sign-in result shows "Google account connected."

- [ ] **Step 3: Types and the test account**

In `frontend/src/api/types.ts`, add to `Account` after `mail_permission`:

```ts
  /** Allow permanent delete is on: the owner's switch, and Google allows it (bulk offload spec §3). */
  permanent_delete: boolean;
  /** Why it went off by itself, when Google took the permission back. */
  permanent_delete_note: string | null;
```

In `frontend/src/test/utils.tsx`, add `permanent_delete: false,` and `permanent_delete_note: null,` to `ACCOUNT`.

- [ ] **Step 4: The switch**

```tsx
// frontend/src/components/settings/PermanentDelete.tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { api, friendlyError } from "../../api/client";
import type { Account } from "../../api/types";
import { OAUTH_RETURN_KEY } from "../../hooks/useConnect";
import { useToast } from "../Toasts";
import { Button, Notice } from "../ui";

export const PERMANENT_OFF_TEXT =
  "Offload moves mail to Gmail's trash. Turn this on to delete it from Gmail directly, so the space comes back at once.";
export const PERMANENT_ON_TEXT = "Offload and Delete forever remove mail from Gmail directly. Gmail's trash isn't used.";
export const PERMANENT_REVOKE_TEXT =
  "Turn off stops CleanSync from using it. Google keeps the permission until you remove CleanSync's access at myaccount.google.com/permissions; then reconnect this account.";
export const PERMANENT_ON_TOAST = "Permanent delete is on for this mailbox.";
export const PERMANENT_DENIED_TOAST = "Google didn't give full Gmail access, so permanent delete stays off. Try again and tick the box for it.";

/** Settings → Accounts → Allow permanent delete (bulk offload spec §3). Turn on may need
 * Google's sign-in for the full Gmail permission; Turn off never revokes it at Google. */
export function PermanentDelete({ account }: { account: Account }) {
  const qc = useQueryClient();
  const toast = useToast();
  const location = useLocation();
  const on = account.permanent_delete;
  const change = useMutation({
    mutationFn: (next: boolean) =>
      api<{ auth_url: string | null }>(`/api/accounts/${account.id}/permanent-delete`, { method: "POST", body: { on: next } }),
    onSuccess: ({ auth_url }) => {
      if (auth_url === null) {
        void qc.invalidateQueries({ queryKey: ["accounts"] });
        return;
      }
      try {
        sessionStorage.setItem(OAUTH_RETURN_KEY, location.pathname + location.search);
      } catch {
        // without storage the person lands on Home afterwards, which is fine
      }
      window.location.assign(auth_url);
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
  return (
    <div role="group" aria-label="Allow permanent delete" className="basis-full space-y-1 rounded-card border border-line p-3">
      <p className="font-semibold">
        Allow permanent delete: <span className={on ? "text-accent" : "text-ink-2"}>{on ? "On" : "Off"}</span>
      </p>
      {account.permanent_delete_note && <Notice tone="warn">{account.permanent_delete_note}</Notice>}
      <p className="text-table text-ink-2">{on ? PERMANENT_ON_TEXT : PERMANENT_OFF_TEXT}</p>
      {on && <p className="text-meta text-ink-2">{PERMANENT_REVOKE_TEXT}</p>}
      <Button disabled={change.isPending} onClick={() => change.mutate(!on)}>
        {on ? "Turn off" : "Turn on"}
      </Button>
    </div>
  );
}
```

In `frontend/src/pages/SettingsPage.tsx`, import `PermanentDelete` from `../components/settings/PermanentDelete`, and in `AccountRow`, right before `{confirm && (`, add:

```tsx
      {account.status === "CONNECTED" && account.services.mail !== "not_set_up" && <PermanentDelete account={account} />}
```

- [ ] **Step 5: What Google answered**

In `frontend/src/routes.tsx`, import `PERMANENT_DENIED_TOAST, PERMANENT_ON_TOAST` from `./components/settings/PermanentDelete`. In `OAuthReturn`, add `const permanent = params.get("permanent");` beside `mail`, add `permanent` to the effect's dependency list, and change the toast lines to:

```tsx
      if (mail === "ready") toast(MAIL_READY_TOAST);
      else if (mail === "denied") toast(MAIL_DENIED_TOAST, { tone: "error" });
      else if (permanent === "on") toast(PERMANENT_ON_TOAST);
      else if (permanent === "denied") toast(PERMANENT_DENIED_TOAST, { tone: "error" });
      else toast("Google account connected.");
```

- [ ] **Step 6: Run the frontend checks**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: PASS. If a test builds an `Account` by hand without the new fields and typecheck complains, add `permanent_delete: false, permanent_delete_note: null` there.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(settings): Allow permanent delete per mailbox in Settings → Accounts

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: The space texts, Stop on an offload, and the chip's Undo

**Files:**
- Modify: `frontend/src/api/types.ts` (`MailOffload.kind`, `removed`, `trashed`; `DecisionChip.undoable`, `forever`)
- Modify: `frontend/src/lib/mail.ts` (`SPACE_FREE`, `spaceLines`)
- Modify: `frontend/src/lib/mail.test.ts`
- Modify: `frontend/src/hooks/useMailOffload.ts` (`useStopOffload`)
- Modify: `frontend/src/pages/MailOffloadPage.tsx` (the space lines, Stop)
- Modify: `frontend/src/pages/MailOffloadPage.test.tsx`
- Modify: `frontend/src/components/mail/MailRecovery.tsx` (the space lines)
- Modify: `frontend/src/components/mail/OffloadMailDialog.tsx` (texts with permanent delete)
- Modify: `frontend/src/components/mail/OffloadMailDialog.test.tsx`
- Modify: `frontend/src/components/mail/RuleChip.tsx` (Undo only when `undoable`; "deleted forever")

**Interfaces:**
- Consumes: Task 4's `kind`, `removed`, `trashed`; Task 5's `POST /api/mail/offloads/{id}/stop`; Task 6's chip `undoable`, `forever`.
- Produces:
  - `MailOffload.kind: "offload" | "delete_forever"`, `MailOffload.removed: number`, `MailOffload.trashed: number`
  - `DecisionChip.undoable: boolean`, `DecisionChip.forever: boolean`
  - `SPACE_FREE = "Removed from Gmail. The space is free now."`, `spaceLines(o): string[]` in `lib/mail.ts`
  - `useStopOffload()` mutating an offload id

- [ ] **Step 1: Write the failing tests**

In `frontend/src/lib/mail.test.ts`, add (import `spaceLines`, `SPACE_FREE` from `./mail`):

```ts
describe("spaceLines", () => {
  const base = { trash_until: "2026-11-12", google_emptied: false };
  it("says the space is free when everything left Gmail for good", () => {
    expect(spaceLines({ ...base, removed: 3, trashed: 0 })).toEqual([SPACE_FREE]);
  });
  it("keeps the trash line when nothing was removed for good", () => {
    expect(spaceLines({ ...base, removed: 0, trashed: 3 })).toEqual([expect.stringMatching(/^In Gmail's trash until about /)]);
  });
  it("shows both, with counts, for a mixed offload", () => {
    const [free, trash] = spaceLines({ ...base, removed: 1200, trashed: 1 });
    expect(free).toBe(`1,200 mails: ${SPACE_FREE}`);
    expect(trash).toMatch(/^1 mail: In Gmail's trash until about /);
  });
  it("says nothing before any mail left", () => {
    expect(spaceLines({ ...base, removed: 0, trashed: 0 })).toEqual([]);
  });
});
```

In `frontend/src/pages/MailOffloadPage.test.tsx`, add `kind: "offload", removed: 0, trashed: 120,` to the `OFF` fixture, and add (the file's `open(id)` helper renders the page):

```tsx
  it("says the space is free for mail removed for good, and offers Stop while it runs", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACCOUNT],
      "GET /api/mail/offloads/5": { ...OFF, id: 5, state: "RUNNING", done: 2, removed: 2, trashed: 0, skipped_items: [] },
      "POST /api/mail/offloads/5/stop": {},
    });
    open(5);
    expect(await screen.findByText("Removed from Gmail. The space is free now.")).toBeInTheDocument();
    expect(screen.queryByText(/Gmail empties its trash on its own/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/mail/offloads/5/stop")).toBe(true));
  });
```

In `frontend/src/components/mail/OffloadMailDialog.test.tsx`, add:

```tsx
  it("tells a mailbox with permanent delete that the space comes back at once", () => {
    mockApi({});
    renderApp(<OffloadMailDialog account={{ ...ACCOUNT, permanent_delete: true }} start={{ q: "older_than:1y" }} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Offload mail" });
    expect(dialog).toHaveTextContent("Offloaded mail is removed from Gmail for good and stays readable here.");
    expect(dialog).not.toHaveTextContent("Gmail empties its trash on its own");
  });
```

(`ACCOUNT`, `mockApi` and `renderApp` are already imported there.)

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd frontend && npm test -- --run src/lib/mail.test.ts src/pages/MailOffloadPage.test.tsx src/components/mail/OffloadMailDialog.test.tsx`
Expected: FAIL: `spaceLines` doesn't exist, the page has no Stop, and the dialog shows the trash text.

- [ ] **Step 3: Types and the space lines**

In `frontend/src/api/types.ts`, add to `MailOffload` after `google_emptied`:

```ts
  /** "delete_forever": mail deleted from Gmail and CleanSync (bulk offload spec §5). */
  kind: "offload" | "delete_forever";
  /** Removed from Gmail for good: the space is free now. */
  removed: number;
  /** In Gmail's trash until Gmail empties it. */
  trashed: number;
```

and change `DecisionChip` to:

```ts
export type DecisionChip = {
  id: number;
  rule_name: string;
  status: "suggested" | "applied" | "planned";
  how: string;
  why: string;
  /** False when the mail left Gmail for good: Undo can't bring it back. */
  undoable: boolean;
  /** The rule's action is Delete forever. */
  forever: boolean;
};
```

In `frontend/src/lib/mail.ts`, after `trashLine`, add:

```ts
export const SPACE_FREE = "Removed from Gmail. The space is free now.";
const mailCount = (n: number) => `${n.toLocaleString()} ${n === 1 ? "mail" : "mails"}`;

/** Bulk offload spec §7: where an offload's space comes back. Mail removed for good frees it
 * now; mail in Gmail's trash when Gmail empties it. A mixed offload gets both, with counts. */
export function spaceLines(o: { removed: number; trashed: number; trash_until: string; google_emptied: boolean }): string[] {
  if (o.trashed === 0) return o.removed > 0 ? [SPACE_FREE] : [];
  if (o.removed === 0) return [trashLine(o)];
  return [`${mailCount(o.removed)}: ${SPACE_FREE}`, `${mailCount(o.trashed)}: ${trashLine(o)}`];
}
```

If any test builds a `MailOffload` or `DecisionChip` by hand and typecheck complains, add `kind: "offload", removed: 0` and `trashed` equal to its `done`, or `undoable: true, forever: false`, there.

- [ ] **Step 4: Stop**

In `frontend/src/hooks/useMailOffload.ts`, add:

```ts
/** Stop an offload or a Delete forever job: a waiting one ends now, a running one before its next batch. */
export function useStopOffload() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (id: number) => api<unknown>(`/api/mail/offloads/${id}/stop`, { method: "POST" }),
    onSuccess: (_d, id) => void qc.invalidateQueries({ queryKey: ["mail-offload", id] }),
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
}
```

- [ ] **Step 5: The offload's page, Recovery and the dialog**

In `frontend/src/pages/MailOffloadPage.tsx`:
- Import `spaceLines` instead of `trashLine` from `../lib/mail`, and `useStopOffload` beside `useRestoreMail`.
- Add `const stop = useStopOffload();` beside `const restore = useRestoreMail();`.
- Replace the `{o.done > 0 && (<div className="space-y-1"> … </div>)}` block with:

```tsx
      {o.done > 0 && (
        <div className="space-y-1">
          {spaceLines(o).map((line) => (
            <p key={line} className="text-ink-2">
              {line}
            </p>
          ))}
          {o.trashed > 0 && <p className="max-w-3xl text-ink-2">{TRASH_NOTE}</p>}
        </div>
      )}
```

- In the buttons row, before "Restore all to Gmail", add:

```tsx
        {busy && (
          <Button disabled={stop.isPending} onClick={() => stop.mutate(o.id)}>
            Stop
          </Button>
        )}
```

In `frontend/src/components/mail/MailRecovery.tsx`, import `spaceLines` instead of `trashLine`, and `{o.done > 0 && <span className="basis-full text-ink-2">{trashLine(o)}</span>}` becomes `{o.done > 0 && <span className="basis-full text-ink-2">{spaceLines(o).join(" ")}</span>}`.

In `frontend/src/components/mail/OffloadMailDialog.tsx`:
- Add `const forGood = account.permanent_delete;` at the top of the component.
- The first paragraph becomes:

```tsx
      <p className="text-ink-2">
        {forGood
          ? "Offloaded mail is removed from Gmail for good and stays readable here. The space comes back at once. You can restore it to Gmail at any time."
          : "Offloaded mail moves to Gmail's trash and stays readable here. Gmail empties its trash on its own after 30 days; that's when the space comes back. You can restore it to Gmail at any time."}
      </p>
```

- The preview's count line ends with `{forGood ? " The space comes back in Gmail at once." : " The space is freed in Gmail once Gmail's trash is emptied."}` in place of the fixed sentence.

- [ ] **Step 6: The chip**

In `frontend/src/components/mail/RuleChip.tsx`:
- `const label = \`${chip.rule_name} · ${chip.status}\`;` becomes:

```tsx
  const label = `${chip.rule_name} · ${chip.forever && chip.status === "applied" ? "deleted forever" : chip.status}`;
```

- `{chip.status === "applied" && (` before the Undo button becomes `{chip.status === "applied" && chip.undoable && (`.
- Change the doc comment's last line to `* Approve, Correct… and (once the rule has acted, and Gmail can still take it back) Undo. */`.

- [ ] **Step 7: Run the frontend checks**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck && grep -rn "trashLine" src --include=*.tsx`
Expected: tests, lint and typecheck PASS; the grep finds no `.tsx` file using `trashLine` directly.

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "feat(mail): say when the space is free, Stop an offload, no Undo for mail removed for good

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Delete forever in the Gmail views and the reader

**Files:**
- Modify: `frontend/src/components/mail/DeleteForeverDialog.tsx` (`FOREVER_OFF`, `ForeverButton`, Gmail views start a job and show its progress)
- Modify: `frontend/src/hooks/useRules.ts` (`useDeleteForever` answer has `job_id`)
- Modify: `frontend/src/components/mail/MailReader.tsx` (Delete forever… in the Gmail views' bar)
- Modify: `frontend/src/components/mail/MailActions.tsx` (Delete forever… on a mail in Gmail)
- Create: `frontend/src/components/mail/DeleteForeverDialog.test.tsx`

**Interfaces:**
- Consumes: Task 7's `POST /api/mail/delete-forever` answer `{deleted, left, job_id}`; Task 10's `useStopOffload`, `MailOffload.kind`; `useMailOffload(id)` (existing); round 1's `DeleteForeverDialog({ account, start, count, onClose, onDone })`, `viewKind`, the bar's `pickForever`.
- Produces:
  - `FOREVER_OFF = "Turn on Allow permanent delete in Settings → Accounts."`
  - `ForeverButton({ account, disabled?, onClick })`: "Delete forever…", disabled with `FOREVER_OFF` as its description when the mailbox doesn't allow permanent delete.
  - `useDeleteForever()` resolves to `{ deleted: number; left: number; job_id: number | null }`.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/src/components/mail/DeleteForeverDialog.test.tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ACCOUNT, mockApi, renderApp } from "../../test/utils";
import { DeleteForeverDialog, ForeverButton } from "./DeleteForeverDialog";

const JOB = {
  id: 7,
  account_id: 1,
  filter_text: "Inbox · from:shop.example.com",
  state: "RUNNING",
  total: 1284,
  done: 330,
  skipped: 10,
  not_done: 944,
  bytes: 0,
  reason: null,
  created_at: "2026-10-13T10:00:00Z",
  finished_at: null,
  trash_until: "2026-11-12",
  google_emptied: false,
  kind: "delete_forever",
  removed: 330,
  trashed: 0,
  skipped_items: [],
};

describe("Delete forever on mail in Gmail", () => {
  it("is offered disabled, with the reason, when the mailbox doesn't allow permanent delete", () => {
    renderApp(<ForeverButton account={ACCOUNT} onClick={() => {}} />);
    const button = screen.getByRole("button", { name: "Delete forever…" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("Turn on Allow permanent delete in Settings → Accounts.");
  });

  it("is offered when it does", async () => {
    const onClick = vi.fn();
    renderApp(<ForeverButton account={{ ...ACCOUNT, permanent_delete: true }} onClick={onClick} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete forever…" }));
    expect(onClick).toHaveBeenCalled();
  });

  it("starts a background job after the typed count, and shows its progress with Stop", async () => {
    const { calls } = mockApi({
      "POST /api/mail/delete-forever": { deleted: 0, left: 0, job_id: 7 },
      "GET /api/mail/offloads/7": JOB,
      "POST /api/mail/offloads/7/stop": {},
    });
    renderApp(
      <DeleteForeverDialog account={{ ...ACCOUNT, permanent_delete: true }} start={{ view: "inbox", q: "from:shop.example.com" }} count={1284} onClose={() => {}} />,
    );
    const dialog = screen.getByRole("dialog", { name: "Delete forever" });
    expect(dialog).toHaveTextContent("1,284 mails in Gmail.");
    expect(dialog).toHaveTextContent("They are deleted from Gmail for good, and CleanSync's copy is deleted too.");
    await userEvent.type(within(dialog).getByLabelText("Type 1,284 to delete forever"), "1284");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete forever" }));
    expect(await screen.findByText("Deleting… 340 of 1,284")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/mail/delete-forever")?.body).toMatchObject({
      account_id: 1,
      view: "inbox",
      q: "from:shop.example.com",
      confirm: 1284,
    });
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/mail/offloads/7/stop")).toBe(true));
  });

  it("says what was deleted and what stayed when the job is done", async () => {
    mockApi({
      "POST /api/mail/delete-forever": { deleted: 0, left: 0, job_id: 7 },
      "GET /api/mail/offloads/7": { ...JOB, state: "DONE_WITH_SKIPS", done: 1283, skipped: 1, not_done: 0 },
    });
    renderApp(<DeleteForeverDialog account={{ ...ACCOUNT, permanent_delete: true }} start={{ view: "all", message_ids: [9] }} count={1} onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText("Type 1 to delete forever"), "1");
    await userEvent.click(within(screen.getByRole("dialog", { name: "Delete forever" })).getByRole("button", { name: "Delete forever" }));
    expect(await screen.findByText("Deleted 1,283 mails forever.")).toBeInTheDocument();
    expect(screen.getByText("1 mail wasn't deleted and stays in Gmail, with CleanSync's copy.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd frontend && npm test -- --run src/components/mail/DeleteForeverDialog.test.tsx`
Expected: FAIL: `ForeverButton` isn't exported, and the dialog speaks only of Offloaded mail.

- [ ] **Step 3: The answer's type**

In `frontend/src/hooks/useRules.ts`, `useDeleteForever`'s `post<{ deleted: number; left: number }>` becomes `post<{ deleted: number; left: number; job_id: number | null }>`.

- [ ] **Step 4: The dialog and the button**

In `frontend/src/components/mail/DeleteForeverDialog.tsx`:
- Imports: `useId` beside `useState` from `react`; `useMailOffload` from `../../api/queries`; `useStopOffload` from `../../hooks/useMailOffload`; `viewKind` from `../../lib/mail`; `percent` from `../../lib/format`; `Bar` and `Notice` added to the `../ui` import.
- After `PLACES`, add:

```tsx
export const FOREVER_OFF = "Turn on Allow permanent delete in Settings → Accounts.";

/** "Delete forever…" on mail in Gmail (bulk offload spec §5): shown disabled, with the
 * reason, for a mailbox that doesn't allow permanent delete. */
export function ForeverButton({ account, disabled = false, onClick }: { account: Account; disabled?: boolean; onClick: () => void }) {
  const id = useId();
  const off = !account.permanent_delete;
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Button tone="danger" disabled={off || disabled} aria-describedby={off ? id : undefined} onClick={onClick}>
        Delete forever…
      </Button>
      {off && (
        <span id={id} className="text-meta text-ink-2">
          {FOREVER_OFF}
        </span>
      )}
    </span>
  );
}

/** The job's progress, then what it did (bulk offload spec §5). */
function ForeverJob({ jobId, count, onClose }: { jobId: number; count: number; onClose: () => void }) {
  const job = useMailOffload(jobId).data;
  const stop = useStopOffload();
  const busy = !job || job.state === "WAITING" || job.state === "RUNNING";
  return (
    <Dialog
      title="Delete forever"
      onClose={onClose}
      footer={
        <>
          {busy && (
            <Button disabled={stop.isPending} onClick={() => stop.mutate(jobId)}>
              Stop
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      {busy || !job ? (
        <>
          <p className="tabular">
            Deleting… {(job ? job.done + job.skipped : 0).toLocaleString()} of {count.toLocaleString()}
          </p>
          <Bar label="Mails deleted" value={percent(job ? job.done + job.skipped : 0, count)} tone="run" />
          <p className="text-ink-2">You can close this. The deleting goes on.</p>
        </>
      ) : (
        <>
          <p>Deleted {mails(job.done)} forever.</p>
          {job.skipped > 0 && (
            <p>
              {mails(job.skipped)} {job.skipped === 1 ? "wasn't" : "weren't"} deleted and {job.skipped === 1 ? "stays" : "stay"} in Gmail, with CleanSync's copy.
            </p>
          )}
          {job.reason && <Notice tone="err">{job.reason}</Notice>}
        </>
      )}
    </Dialog>
  );
}
```

- In `DeleteForeverDialog`, add `const inGmail = viewKind(start.view) === "gmail";` and `const [jobId, setJobId] = useState<number | null>(null);`. The `onSuccess` becomes:

```tsx
        onSuccess: ({ deleted, job_id }) => {
          onDone?.();
          if (job_id !== null) return setJobId(job_id); // mail in Gmail: a background job
          toast(`Deleted ${mails(deleted)} forever.`);
          onClose();
        },
```

- Right before the component's `return (`, add `if (jobId !== null) return <ForeverJob jobId={jobId} count={count} onClose={onClose} />;`.
- The two body paragraphs become:

```tsx
      <p>{inGmail ? `${mails(count)} in Gmail.` : `${mails(count)} from ${PLACES[start.view] ?? start.view}.`}</p>
      <p className="text-ink-2">
        {inGmail
          ? "They are deleted from Gmail for good, and CleanSync's copy is deleted too. Afterwards they exist nowhere. This can't be undone."
          : "This removes CleanSync's own copy. The mail is already gone from Gmail, so afterwards it exists nowhere. This can't be undone."}
      </p>
```

- The doc comment becomes `/** Delete forever (mail panel spec §7, bulk offload spec §5). In Offloaded and Deleted in Gmail it removes CleanSync's copy at once; in a Gmail view it starts a job that removes the mail from Gmail, then the copy, and shows its progress. Picked mails (`message_ids`), or a pick-all as the view, label and search. `count` is how many; the person types it, and the server counts again. `onDone` runs once the server took it. */`

- [ ] **Step 5: Where it's offered**

In `frontend/src/components/mail/MailReader.tsx`, import `ForeverButton` beside `DeleteForeverDialog`. In the picked-mail bar's `kind === "gmail"` fragment (Offload, Make a rule…, and round 2's Run rules), add as its last button:

```tsx
              <ForeverButton account={account} disabled={finding || (all && totals === undefined)} onClick={pickForever} />
```

Round 1's `pickForever` already works here: a pick-all goes as the view, label and search, and picked threads are read as the mails of this view, which in a Gmail view are the mails in Gmail.

In `frontend/src/components/mail/MailActions.tsx`, import `ForeverButton` beside `DeleteForeverDialog`. In `MessageMailActions`, the `message.state === "in_mail"` branch becomes:

```tsx
        <>
          <Button onClick={() => setOpen("offload")}>Offload</Button>
          <Button onClick={() => setOpen("rule")}>Make a rule…</Button>
          <ForeverButton account={account} onClick={() => setOpen("forever")} />
        </>
```

and `const view = message.state === "deleted_in_provider" ? "deleted" : "offloaded";` becomes:

```tsx
  const view = message.state === "in_mail" ? "all" : message.state === "deleted_in_provider" ? "deleted" : "offloaded";
```

- [ ] **Step 6: Run the frontend checks**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: PASS. If a round 1 test in `MailReader.test.tsx` counts the bar's buttons in a Gmail view, add "Delete forever…" to its expected list.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(mail): Delete forever on mail in Gmail, with progress and Stop

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: The rule form's Delete forever, the rule card's warning, and approving it

**Files:**
- Modify: `frontend/src/api/rules.ts` (`Rule.blocked`)
- Modify: `frontend/src/hooks/useRules.ts` (`useApprove` sends `confirm`)
- Modify: `frontend/src/components/rules/RuleForm.tsx` (Delete forever with the permission; Automatic off for it)
- Modify: `frontend/src/components/rules/RuleForm.test.tsx`
- Modify: `frontend/src/components/rules/RuleList.tsx` (the card shows `blocked`)
- Modify: `frontend/src/components/rules/Rules.test.tsx`
- Create: `frontend/src/components/mail/ApproveForeverDialog.tsx`, `frontend/src/components/mail/ApproveForeverDialog.test.tsx`
- Modify: `frontend/src/components/mail/MailReader.tsx` (Approve all and Approve ask first)
- Modify: `frontend/src/components/mail/RuleChip.tsx` (Approve asks first for a Delete forever chip)

**Interfaces:**
- Consumes: Task 8's `blocked`, `POST /api/rule-decisions/approve` with `confirm`; Task 10's `DecisionChip.forever`; Task 11's `FOREVER_OFF`; `useAccounts()` (existing); round 1's RuleForm (`off`, `pickOffload`, the "What it does" fieldset).
- Produces:
  - `Rule.blocked: string | null`
  - `useApprove()` mutating `{ accountId, ids, confirm? }`
  - `ApproveForeverDialog({ accountId, ids, forever, onClose, onDone? })`
  - `FOREVER_SUGGEST_ONLY = "A Delete forever rule always suggests first."` in `RuleForm.tsx`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/rules/RuleForm.test.tsx`, in round 1's test "lists the actions in order, with Delete forever shown but not yet available", rename it "lists the actions in order, with Delete forever off until the mailbox allows it", give its `mockApi` the route `"GET /api/accounts": [ACCOUNT]`, and change the description check to:

```tsx
    expect(forever).toHaveAccessibleDescription("Turn on Allow permanent delete in Settings → Accounts.");
```

Then add (with `ACCOUNT` in the `../../test/utils` import):

```tsx
  it("offers Delete forever once the mailbox allows it, on its own and always Suggest first", async () => {
    const { calls } = mockApi({ "GET /api/accounts": [{ ...ACCOUNT, permanent_delete: true }], "POST /api/rules": SAVED });
    renderApp(<RuleForm accountId={1} initial={DRAFT} onSaved={() => {}} onCancel={() => {}} />);
    const forever = await screen.findByRole("checkbox", { name: "Delete forever" });
    await waitFor(() => expect(forever).toBeEnabled());
    await userEvent.click(screen.getByRole("radio", { name: "Automatic" }));
    await userEvent.click(forever);
    expect(screen.getByRole("checkbox", { name: "Archive" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Automatic" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Automatic" })).toHaveAccessibleDescription("A Delete forever rule always suggests first.");
    expect(screen.getByRole("radio", { name: /Suggest first/ })).toBeChecked();
    await userEvent.click(screen.getByRole("checkbox", { name: "Star" }));
    expect(forever).not.toBeChecked();
    await userEvent.click(forever);
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "/api/rules")?.body).toMatchObject({ actions: [{ kind: "delete_forever" }], mode: "suggest" }),
    );
  });
```

In `frontend/src/components/rules/Rules.test.tsx`, add:

```tsx
  it("says on the card why a Delete forever rule waits", async () => {
    mockApi({
      ...ACCOUNTS,
      "GET /api/rules": [{ ...RULE, actions: [{ kind: "delete_forever" }], blocked: "Turn on Allow permanent delete in Settings → Accounts." }],
      "GET /api/rules/sorting": SORTING,
    });
    renderApp(<MailRulesPage />, { withAccount: true });
    expect(await screen.findByText("Turn on Allow permanent delete in Settings → Accounts.")).toBeInTheDocument();
    expect(screen.getByText("Delete forever")).toBeInTheDocument();
  });
```

```tsx
// frontend/src/components/mail/ApproveForeverDialog.test.tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { mockApi, renderApp } from "../../test/utils";
import { ApproveForeverDialog } from "./ApproveForeverDialog";

describe("approving Delete forever suggestions", () => {
  it("asks for the count before approving Delete forever suggestions", async () => {
    const { calls } = mockApi({ "POST /api/rule-decisions/approve": { applied: 5 } });
    const onDone = vi.fn();
    renderApp(<ApproveForeverDialog accountId={1} ids={[1, 2, 3, 4, 5]} forever={3} onClose={() => {}} onDone={onDone} />);
    const dialog = screen.getByRole("dialog", { name: "Approve" });
    expect(dialog).toHaveTextContent("3 mails of these will be deleted forever: from Gmail and from CleanSync. This can't be undone.");
    const go = within(dialog).getByRole("button", { name: "Approve" });
    expect(go).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Type 3 to delete them forever"), "3");
    await userEvent.click(go);
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/rule-decisions/approve")?.body).toEqual({ account_id: 1, ids: [1, 2, 3, 4, 5], confirm: 3 }),
    );
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });
});
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd frontend && npm test -- --run src/components/rules src/components/mail/ApproveForeverDialog.test.tsx`
Expected: FAIL: Delete forever stays disabled, the card shows no warning, and `ApproveForeverDialog` doesn't exist.

- [ ] **Step 3: Types and the approve hook**

In `frontend/src/api/rules.ts`, add to `Rule`:

```ts
  /** Why the rule waits (a Delete forever rule while its mailbox doesn't allow it). */
  blocked?: string | null;
```

In `frontend/src/hooks/useRules.ts`, `useApprove` becomes:

```ts
export const useApprove = () =>
  useChange((b: Acc & { ids: number[]; confirm?: number }) =>
    post<ApproveResult>("/api/rule-decisions/approve", {
      account_id: b.accountId,
      ids: b.ids,
      ...(b.confirm !== undefined ? { confirm: b.confirm } : {}),
    }),
  );
```

- [ ] **Step 4: The approve dialog**

```tsx
// frontend/src/components/mail/ApproveForeverDialog.tsx
import { useState } from "react";

import { useApprove } from "../../hooks/useRules";
import { Button, Dialog, Field, inputClass } from "../ui";
import { parseTyped } from "./OffloadMailDialog";

const mails = (n: number) => `${n.toLocaleString()} ${n === 1 ? "mail" : "mails"}`;

/** Approving suggestions that include Delete forever (plan decision 16): the count of those
 * mails is typed first, and the server checks it again. */
export function ApproveForeverDialog({
  accountId,
  ids,
  forever,
  onClose,
  onDone,
}: {
  accountId: number;
  ids: number[];
  forever: number;
  onClose: () => void;
  onDone?: () => void;
}) {
  const [typed, setTyped] = useState("");
  const approve = useApprove();
  const ok = parseTyped(typed) === forever;
  const which = ids.length === forever ? mails(forever) : `${mails(forever)} of these`;
  return (
    <Dialog
      title="Approve"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            tone="danger"
            disabled={!ok || approve.isPending}
            onClick={() =>
              approve.mutate(
                { accountId, ids, confirm: forever },
                {
                  onSuccess: () => {
                    onDone?.();
                    onClose();
                  },
                },
              )
            }
          >
            Approve
          </Button>
        </>
      }
    >
      <p>{which} will be deleted forever: from Gmail and from CleanSync. This can't be undone.</p>
      <Field label={`Type ${forever.toLocaleString()} to delete them forever`}>
        <input className={`${inputClass} w-40`} inputMode="numeric" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
      </Field>
    </Dialog>
  );
}
```

- [ ] **Step 5: Approve asks first**

In `frontend/src/components/mail/MailReader.tsx`, import `ApproveForeverDialog`. Add beside the other state:

```tsx
  const [askForever, setAskForever] = useState<{ ids: number[]; forever: number } | null>(null);
  const foreverIds = new Set(rows.flatMap((t) => (t.decision?.forever ? [t.decision.id] : [])));
  // Delete forever suggestions need their count typed first (plan decision 16).
  const approveIds = (ids: number[], done?: () => void) => {
    const forever = ids.filter((id) => foreverIds.has(id)).length;
    if (forever > 0) return setAskForever({ ids, forever });
    approve.mutate({ accountId: account.id, ids }, { onSuccess: done });
  };
```

"Approve all"'s `onClick` becomes `() => approveIds(suggestedIds)`, and the bar's Approve `onClick` becomes `() => approveIds(pickedSuggestions, clearPicks)`. Next to the other dialogs, add:

```tsx
      {askForever && (
        <ApproveForeverDialog accountId={account.id} ids={askForever.ids} forever={askForever.forever} onDone={clearPicks} onClose={() => setAskForever(null)} />
      )}
```

In `frontend/src/components/mail/RuleChip.tsx`, import `ApproveForeverDialog`, add `const [asking, setAsking] = useState(false);`, and the Approve button's `onClick` becomes:

```tsx
                  onClick={() => {
                    setAt(null);
                    if (chip.forever) return setAsking(true);
                    approve.mutate({ accountId, ids: [chip.id] }, done);
                  }}
```

and after the `{correctAt && (…)}` block add:

```tsx
      {asking && <ApproveForeverDialog accountId={accountId} ids={[chip.id]} forever={1} onDone={onChanged} onClose={() => setAsking(false)} />}
```

- [ ] **Step 6: The rule form and the card**

In `frontend/src/components/rules/RuleForm.tsx`:
- Import `useAccounts` from `../../api/queries` and `FOREVER_OFF` from `../mail/DeleteForeverDialog`.
- Add after the imports: `export const FOREVER_SUGGEST_ONLY = "A Delete forever rule always suggests first.";`
- Add state and the permission beside `off`:

```tsx
  const [forever, setForever] = useState(initial.actions.some((a) => a.kind === "delete_forever"));
  const allowForever = useAccounts().data?.find((a) => a.id === accountId)?.permanent_delete ?? false;
```

- `pickOffload` also clears `forever` when on (`if (on) { setSimple(new Set()); setLabelOn(false); setForever(false); }`), and `pickSimple` and `pickLabel` also call `setForever(false)` when `on`. Add:

```tsx
  // Delete forever stands alone too, and always suggests first (bulk offload spec §6).
  const pickForever = (on: boolean) => {
    setForever(on);
    if (on) {
      setSimple(new Set());
      setLabelOn(false);
      setOff(false);
      setMode("suggest");
    }
  };
```

- `actions` becomes:

```tsx
  const actions: RuleAction[] = forever
    ? [{ kind: "delete_forever" }]
    : off
      ? [{ kind: "offload" }]
      : [...SIMPLE.filter(([k]) => simple.has(k)).map(([kind]) => ({ kind })), ...(labelOn ? [{ kind: "label" as const, label: labelName.trim() }] : [])];
```

- Replace round 1's disabled Delete forever `<label>` (the one under the comment `Round 3 of 0.5.0 turns this on`) with:

```tsx
          <label className={`flex items-center gap-2 ${allowForever || forever ? "" : "text-ink-2"}`}>
            <input
              type="checkbox"
              checked={forever}
              disabled={!allowForever && !forever}
              aria-describedby={`${id}-forever`}
              onChange={(e) => pickForever(e.target.checked)}
            />
            Delete forever
          </label>
```

  (a rule that already has it can still be unticked after the permission is gone).
- Replace the `id={`${id}-forever`}` paragraph's text with:

```tsx
          {allowForever ? "Delete forever removes the mail from Gmail and CleanSync's copy. Nothing is left to restore." : FOREVER_OFF}
```

- The Automatic radio becomes:

```tsx
            <input
              type="radio"
              name={`${id}-mode`}
              checked={mode === "automatic"}
              disabled={forever}
              aria-describedby={forever ? `${id}-suggest-only` : undefined}
              onChange={() => setMode("automatic")}
            />
            Automatic
```

  and right after the Mode fieldset's `</div>`, add `{forever && <p id={`${id}-suggest-only`} className="mt-1 text-meta text-ink-2">{FOREVER_SUGGEST_ONLY}</p>}`.

In `frontend/src/components/rules/RuleList.tsx`, import `Notice` from `../ui` (beside what it imports there), and right after the card's `<p className="text-table">{rule.actions.map(describeAction).join(", ")}</p>`, add:

```tsx
                    {rule.blocked && <Notice tone="warn">{rule.blocked}</Notice>}
```

- [ ] **Step 7: Run the frontend checks**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck && grep -rn "coming in this release" src`
Expected: tests, lint and typecheck PASS; the grep finds nothing.

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "feat(rules): pick Delete forever in the rule form, see why it waits, type the count to approve

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 13: The browser test and the docs

**Files:**
- Modify: `frontend/e2e/helpers.ts` (`fakeGmailMails`; `fakeGmailTrash` uses it)
- Create: `frontend/e2e/permanent-delete.spec.ts`
- Modify: `frontend/playwright.config.ts` (a project after the rules test)
- Modify: `docs/google-oauth-setup.md`, `docs/mail-backup.md`, `docs/mail-rules.md`, `docs/security.md`, `docs/limitations.md`

**Interfaces:**
- Consumes: everything above, through the real app and the fake Gmail (Task 1 gave it `batchDelete` and `batchModify`; Task 3 gives the pretend account the scope without Google).
- Produces: `fakeGmailMails(): { subject: string; labels: string[] }[] | null` in `e2e/helpers.ts`.

- [ ] **Step 1: The fake Gmail reader**

In `frontend/e2e/helpers.ts`, replace `fakeGmailTrash` with:

```ts
/** Every mail the pretend Gmail holds, with its labels. Needs E2E_CONTAINER (the container's
 * name) to look inside, or E2E_FAKE_GMAIL (the mailbox file, for a CleanSync run without Docker);
 * without either this returns null and the test skips that check (in CI it throws). */
export function fakeGmailMails(): { subject: string; labels: string[] }[] | null {
  const name = process.env.E2E_CONTAINER;
  const file = process.env.E2E_FAKE_GMAIL;
  if (!name && !file) {
    // In CI a missing name would silently skip the Gmail checks, so fail instead.
    if (process.env.CI) throw new Error("E2E_CONTAINER must name the container in CI, or the trash checks are skipped");
    return null;
  }
  const json = name
    ? execFileSync("docker", ["exec", name, "cat", "/drivesync/app/data/fake-gmail/alice@example.com.json"], {
        encoding: "utf8",
        maxBuffer: 256 * 1024 * 1024,
      })
    : fs.readFileSync(file!, "utf8");
  const data = JSON.parse(json) as { messages: { labels: string[]; raw: string }[] };
  return data.messages.map((m) => ({
    labels: m.labels,
    subject: /^Subject: (.*)$/m.exec(Buffer.from(m.raw, "base64").toString("utf8"))?.[1]?.trim() ?? "",
  }));
}

/** Subjects of the mails the pretend Gmail holds in its trash (null: see fakeGmailMails). */
export function fakeGmailTrash(): string[] | null {
  const mails = fakeGmailMails();
  return mails && mails.filter((m) => m.labels.includes("TRASH")).map((m) => m.subject);
}
```

If round 1's `fakeGmailDelete` sits between these functions, keep it as it is.

- [ ] **Step 2: Write the browser test**

```ts
// frontend/e2e/permanent-delete.spec.ts
import { expect, type Page, test } from "@playwright/test";

import { fakeGmailMails, ready, setUpMail } from "./helpers";

// Bulk offload spec §11: with the fake Gmail, offload a search with permanent delete on, see
// "Removed from Gmail", then restore. Runs after the rules test: it changes the sample mailbox.
const SUBJECT = "Holiday plans";

async function aliceId(page: Page): Promise<number> {
  const accounts = (await (await page.request.get("/api/accounts")).json()) as { id: number; email: string }[];
  const found = accounts.find((a) => a.email === "alice@example.com");
  expect(found).toBeDefined();
  return found!.id;
}

async function csrf(page: Page): Promise<string> {
  return ((await (await page.request.get("/api/auth/me")).json()) as { csrf_token: string }).csrf_token;
}

function inGmail(subject: string): boolean | null {
  const mails = fakeGmailMails();
  return mails && mails.some((m) => m.subject === subject && !m.labels.includes("TRASH"));
}

test("permanent delete: turn it on, offload a search, the space is free at once, restore it", async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await ready(page);
  await setUpMail(page);
  const id = await aliceId(page);
  const headers = { "X-CSRF-Token": await csrf(page) };

  // An earlier test may have offloaded it: put it back in Gmail first.
  const back = await page.request.post("/api/mail/restore", { headers, data: { account_id: id, view: "kept", q: SUBJECT } });
  expect([202, 409], await back.text()).toContain(back.status());
  await expect
    .poll(
      async () => {
        const r = await page.request.get(`/api/mail/threads?account_id=${id}&view=all&q=${encodeURIComponent(SUBJECT)}`);
        return ((await r.json()) as { threads: { subject: string }[] }).threads.map((t) => t.subject);
      },
      { timeout: 60_000 },
    )
    .toContain(SUBJECT);

  // Settings → Accounts: the pretend account gets the permission without Google's sign-in.
  await page.goto("/settings?tab=accounts");
  const box = page.getByRole("group", { name: "Allow permanent delete" });
  await expect(box).toContainText("Offload moves mail to Gmail's trash.");
  await box.getByRole("button", { name: "Turn on" }).click();
  await expect(box).toContainText("Offload and Delete forever remove mail from Gmail directly. Gmail's trash isn't used.");

  // Offload the search: the preview says the space comes back at once.
  await page.goto(`/mail?q=${encodeURIComponent(SUBJECT)}`);
  await page.getByRole("button", { name: "Offload mail…" }).click();
  const dialog = page.getByRole("dialog", { name: "Offload mail" });
  await expect(dialog).toContainText("Offloaded mail is removed from Gmail for good and stays readable here.");
  await dialog.getByRole("button", { name: "Preview" }).click();
  const line = dialog.getByText(/^\d+ mails?, .* The space comes back in Gmail at once\.$/);
  await expect(line).toBeVisible();
  const count = Number(/^(\d+)/.exec((await line.textContent()) ?? "")?.[1]);
  expect(count).toBeGreaterThan(0);
  await dialog.getByLabel(`To start, type ${count}`).fill(String(count));
  await dialog.getByRole("button", { name: "Start offload" }).click();

  // The offload's page: done, removed from Gmail, no trash line.
  await expect(page.getByRole("heading", { name: "Mail offload" })).toBeVisible();
  await expect(page.getByText("Done", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Removed from Gmail. The space is free now.")).toBeVisible();
  await expect(page.getByText(/In Gmail's trash until about/)).toHaveCount(0);
  const gone = fakeGmailMails();
  if (gone) expect(gone.map((m) => m.subject)).not.toContain(SUBJECT);

  // Restore: Gmail no longer has it, so it is imported again from the copy here.
  await page.getByRole("button", { name: "Restore all to Gmail" }).click();
  await expect(page.getByText(/^Putting \d+ mails? back in Gmail\./)).toBeVisible();
  if (inGmail(SUBJECT) !== null) await expect.poll(() => inGmail(SUBJECT), { timeout: 60_000 }).toBe(true);
  await expect
    .poll(
      async () => {
        const r = await page.request.get(`/api/mail/threads?account_id=${id}&view=all&q=${encodeURIComponent(SUBJECT)}`);
        return ((await r.json()) as { threads: { subject: string }[] }).threads.map((t) => t.subject);
      },
      { timeout: 60_000 },
    )
    .toContain(SUBJECT);

  // Turn it off again: later runs start from the default.
  await page.goto("/settings?tab=accounts");
  await box.getByRole("button", { name: "Turn off" }).click();
  await expect(box).toContainText("Offload moves mail to Gmail's trash.");
});
```

In `frontend/playwright.config.ts`, add to `projects` after the `rules` project:

```ts
    // After the rules test: it removes mail from the pretend Gmail for good, then restores it.
    { name: "permanent-delete", testMatch: "permanent-delete.spec.ts", dependencies: ["rules"], use: { ...devices["Desktop Chrome"] } },
```

- [ ] **Step 3: Run the browser test**

Run (with CleanSync and the pretend model running as for the other browser tests, see `frontend/playwright.config.ts`): `cd frontend && npx playwright test --project=permanent-delete`
Expected: PASS. Then run the whole set once, `cd frontend && npx playwright test`, and expect PASS: the smoke and rules tests are unchanged by this task.

- [ ] **Step 4: The docs**

`docs/google-oauth-setup.md`: step 6 becomes "For mail backup, also enable the Gmail API and add the `gmail.modify` scope; for **Allow permanent delete** (optional) also add `https://mail.google.com/`. See [Setting up mail backup](mail-backup.md)."

`docs/mail-backup.md`:
- In "1. Let your Google client read mail", add a step 3: "Optional, for **Allow permanent delete**: add `https://mail.google.com/` the same way. Google calls it "Read, compose, send and permanently delete all your email from Gmail". CleanSync uses it only to delete mail from Gmail for good when you offload it or delete it forever, and only for mailboxes where you turn it on. It never sends mail."
- Replace the paragraph that starts "`gmail.modify` lets CleanSync read your mail" with: "`gmail.modify` lets CleanSync read your mail, move offloaded mail to Gmail's trash, take it out again, and put mail back (import). It does not let CleanSync send mail or empty Gmail's trash."
- New section "Allow permanent delete", after "3. Start": "Settings → Accounts shows **Allow permanent delete** for each mailbox with mail backup. It is off at first: offloads move mail to Gmail's trash, and the space comes back when Gmail empties its trash 30 days later. **Turn on** asks Google for the full Gmail permission for that mailbox. Once Google gives it, offloads delete mail from Gmail directly, so the space comes back at once, and **Delete forever** works on mail still in Gmail. CleanSync still checks its own copy of each mail before anything leaves Gmail. **Turn off** stops CleanSync from using the permission; Google keeps it until you remove CleanSync's access at myaccount.google.com/permissions (then reconnect the account). If Google stops allowing it, CleanSync turns the switch off by itself, says so in Settings → Accounts and in Activity, and goes back to Gmail's trash."
- "Offloading mail": the "For each mail CleanSync first checks its copy here…" paragraph becomes: "CleanSync works through an offload in batches of up to 1,000 mails. For each batch it first checks every copy here (there, same size, same checksum, reads as a mail). Mail whose copy fails stays in Gmail and is listed on the offload. Then one Gmail call removes the rest of the batch: for good with Allow permanent delete, else to Gmail's trash. If Gmail refuses the batch, CleanSync goes one mail at a time, so one bad mail can't hold up the others. **Stop** on the offload's page stops it before its next batch. After a restart an offload goes on by itself."
- The bold paragraph "**Offloaded mail sits in Gmail's trash. …**" becomes: "**Without Allow permanent delete, offloaded mail sits in Gmail's trash.** Gmail empties its trash on its own after 30 days; that's when the space comes back. With it, the offload's page says "Removed from Gmail. The space is free now." CleanSync never empties Gmail's trash for offloads made before you turned it on."
- "Delete forever" section (round 1): add at the end: "With Allow permanent delete, **Delete forever…** also works on mail still in Gmail, in Gmail's views (on picked mail, Pick all, or a mail open in the reader). CleanSync deletes the mail from Gmail first, in batches of up to 1,000, then its own copy. A mail Gmail doesn't delete keeps its copy and is listed. It runs in the background with progress and **Stop**. Without the permission the button is shown disabled."
- "Putting mail back": add "Mail removed from Gmail for good is imported again from its stored `.eml`; the summary says "imported again"."
- "Cost": add "An offload batch costs 50 units for up to 1,000 mails (Gmail's `batchDelete` or `batchModify`); one mail deleted for good costs 10."
- "Limits to know about": the last bullet's last sentence becomes "The only ways a copy is removed are **Delete forever** (which you confirm by typing the count) and a **Delete forever** rule you approve."

`docs/mail-rules.md`:
- §2 "What it does": replace round 1's sentence about Delete forever being listed but not available with: "**Delete forever** deletes the mail from Gmail and CleanSync's copy. It needs **Allow permanent delete** on for the mailbox (Settings → Accounts), always suggests first (it can't be Automatic), and stands alone. Approving it asks you to type how many mails will be deleted forever. It can't be undone."
- §5 "Offload and Delete forever": add "With Allow permanent delete, a rule's Offload and Offload on one mail delete the mail from Gmail directly instead of moving it to the trash. Undo can't bring such a mail back; use **Restore to Gmail** in Offloaded. If the mailbox stops allowing permanent delete, Delete forever rules stop applying: their suggestions wait with "Turn on Allow permanent delete in Settings → Accounts." and the rule card says the same."

`docs/security.md`: replace "The model never sends mail and can't remove mail from Gmail for good." with "The model never sends mail. It can only pick a rule; a rule that deletes forever always waits for your approval." and add a bullet after "Google logins": "**Full Gmail access** (`https://mail.google.com/`) is opt-in per mailbox (Settings → Accounts → Allow permanent delete). Without it CleanSync has `gmail.modify` and can't delete mail from Gmail for good. With it, CleanSync uses it only to delete mail for good when you offload it or delete it forever. It still checks its own copy of each mail first, and it never removes its copy of mail that is still in Gmail. Turning it off stops CleanSync from using it; removing it at Google needs myaccount.google.com/permissions."

`docs/limitations.md`, Mail section:
- Replace "Offloaded mail goes to Gmail's trash and Gmail empties it after 30 days. CleanSync cannot empty it sooner: that needs Google's full mailbox permission, and CleanSync asks only for `gmail.modify`." with "Without Allow permanent delete, offloaded mail goes to Gmail's trash and Gmail empties it after 30 days. With it, offloads delete mail from Gmail directly. CleanSync doesn't empty Gmail's trash for offloads made before it was turned on."
- Replace "If CleanSync stops right after moving a mail to Gmail's trash but before marking it offloaded, that mail shows as "Deleted in Gmail" after the next check; restore it from there if needed." with "If CleanSync stops in the middle of an offload, the offload goes on by itself after the restart and sends its last batch again; Gmail takes that quietly."
- Add: "Turning Allow permanent delete off doesn't take the permission back at Google; remove CleanSync's access at myaccount.google.com/permissions for that."

In the Mail rules section, add: "A Delete forever rule can't be Automatic."

- [ ] **Step 5: Run every check**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && cd ../frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/e2e frontend/playwright.config.ts docs/google-oauth-setup.md docs/mail-backup.md docs/mail-rules.md docs/security.md docs/limitations.md
git commit -m "test(e2e): offload with permanent delete and restore; docs for full Gmail access

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
