# Mail Panel, Search Filters, Select All, Offload and Delete Forever Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Mail panel looks like the mailbox (Gmail's views, then CleanSync's own), a form builds the search, one tick picks a whole search result, and mail removed from Gmail with a copy kept has one name and one state: Offloaded.

**Architecture:**
- The backend search (`search.py`) decides what each view holds: Gmail views only ever show mail in state `IN_MAIL`; a new `archived` view and `in:archived` keyword; `offloaded`, `deleted` and an internal `kept` view (both) for mail out of Gmail.
- Migration 0011 merges `LOCAL_ARCHIVE` into `OFFLOADED` and renames the stored rule action `delete` to `offload`. The rule applier is the one path for a rule's Offload and for Offload on one mail; the offload job is the path for Offload on a filter or a pick.
- A pick-all travels as `{view, label, q}`. The offload start, restore and the new `POST /api/mail/delete-forever` work it out on the server when they start.
- The frontend gets a two-group panel, a small search reader/writer (`lib/mailSearch.ts`) behind a "Search options" form, a three-state Pick all, and an action bar that depends on the view.

**Tech Stack:** FastAPI, SQLAlchemy 2 + Alembic on SQLite, pytest (`asyncio_mode = "auto"`), React 18 + TanStack Query + Tailwind, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-13-mail-panel-and-search-design.md` (approved; round 1 of 3 of 0.5.0). Rounds 2 and 3 (`2026-10-13-running-rules-design.md`, `2026-10-13-bulk-offload-design.md`) are not built here.

## Global Constraints

- **Words in the app:** nothing is called "Delete". Mail is **Offloaded** (out of Gmail, copy kept) or **Deleted forever** (gone everywhere). **Archive** is Gmail's archive. **Deleted in Gmail** is mail the owner deleted in Gmail.
- **Gmail views** (Inbox, Starred, Important, Sent, Archived, All mail, label views, and Suggested) list, count and search only mail in state `IN_MAIL`.
- **Archived:** mail in Gmail whose labels include neither `INBOX` nor `SENT`; search key `in:archived`; listed by conversation; a thread shows when any of its mails is archived.
- **`LOCAL_ARCHIVE`** stays in the `MailState` enum so old backups still read, and no code uses it after Task 2 (only migration 0011 names its value).
- **Rule actions:** `archive`, `label`, `mark_read`, `star`, `offload`; `delete_forever` is listed in the form but refused until round 3 with "Needs full Gmail access (coming in this release)". YAML import reads `delete` as `offload`. Offload stands alone.
- **Pick-all** is sent as `{view, label, q}`, never as ids. A pick (all or by hand) clears when the view, label or search changes.
- **Delete forever:** only in the views `offloaded` and `deleted` in this round; the typed count must match; it records up to 500 senders, subjects and dates plus how many more; it refuses while a restore is running or queued for that mailbox: "A restore is running for this mailbox. Try again when it's done."
- **Copy, exactly:** "Pick all", "Search options", "All 12,430 mails in this search are picked.", "48 threads picked", "Offloaded mail is in the Offloaded view.", "Needs full Gmail access (coming in this release)", the panel label "CleanSync".
- **Migration:** `0011`, `down_revision = "0010"`.
- **Not in this round:** Run on a rule card, the rule picker, the match list, `batchDelete`, permanent delete, the Delete forever rule action working. "Run rules on…" stays as it is.
- **Copy style:** plain everyday words, matching the existing UI text.
- **Tests must pass:** backend `cd backend && uv run pytest -q`, lint `uv run ruff check . && uv run ruff format --check .`. Frontend `cd frontend && npm test -- --run && npm run lint && npm run typecheck`.
- **Commits:** every commit message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Plan decisions on the spec

These settle details the spec left open. Reviewers judge against them.

1. **Order.** Task 1 changes the views while the Local archive still works. Task 2 then merges it into Offloaded. So every commit leaves a working app.
2. **An internal view `kept`** (Offloaded and Deleted in Gmail together) is the default for Restore. It isn't in the panel. Old restore calls with `q="is:offloaded"` or `q="is:deleted"` keep working, and Recovery's "Restore all to Gmail" sends `{view: "deleted", q: ""}`.
3. **`is:deleted` in a Gmail view** says "Mail deleted in Gmail is in the Deleted in Gmail view.", the twin of the spec's Offloaded message.
4. **`in:` only knows `archived`** ('"in:" only knows in:archived.'). `is:archived` (the old Local archive) goes away in Task 2 and then gets the usual '"is:" knows unread, starred, offloaded and deleted.'
5. **Suggested counts and lists only mail in Gmail.** A suggestion for mail that left Gmail can't be applied.
6. **The search stays in the current view and label.** Searching from Inbox searches the Inbox. Round 2 needs this ("every mail of the search in that view"). An empty search clears the search. Before, every search jumped to All mail.
7. **Opening a thread passes the view** (`GET /api/mail/threads/{id}?view=`): in a Gmail view you see the thread's mails in Gmail, in Offloaded its offloaded mails, in Deleted in Gmail its deleted ones. The old `archive=true` parameter is dropped.
8. **Delete forever moves to `POST /api/mail/delete-forever`**, in a new module `forever.py`. `/api/mail/archive/delete-forever` and `archive.py` go. With `message_ids`, only mails in the view's state are deleted and the rest come back as `left`. An empty search in the view means the whole view.
9. **A pick-all in All mail with no search and no label is refused** for Offload with today's "Choose at least one filter, or pick mails in the list." One tick shouldn't offload a whole mailbox. Inbox, Archived or a label with no search is allowed.
10. **An offload's `filter_text` from a pick-all** names the view, the label and the search joined by " · ", for example "Inbox · older_than:1y" or "Receipts". All mail adds no name, so today's "older_than:1y" stays as it is.
11. **Offload on one mail** in the reader is `POST /api/mail/messages/{id}/offload`, through the applier, after a plain confirm (no typed count), as Delete was. Like Delete, it doesn't apply the 30-day minimum age.
12. **The reader:** a mail in Gmail offers Offload and Make a rule…; an offloaded or Deleted-in-Gmail mail offers Restore to Gmail and Delete forever….
13. **The header:** "Restore these to Gmail" goes (Pick all then Restore replaces it). "Offload mail…" stays, in Gmail views only (not Suggested). "Run rules on…" is untouched until round 2.
14. **Suggested has no Pick all**, because Approve all already covers the whole list. Its bar offers **Approve** for rows picked by hand.
15. **Make a rule… uses up to 10 picked mails:** the newest mail of each picked thread among the rows loaded, newest first.
16. **The bar text with no search** says "this view": "All 1,204 mails in this view are picked." One mail: "The 1 mail in this search is picked." Unknown total (still loading): "All mails in this search are picked."
17. **Batches tab** is `/mail?view=offloaded&tab=batches`. `/mail/offloads` redirects there. An offload's own page stays at `/mail/offloads/:id`, and the Offloaded panel link is lit on all three.
18. **Undo of an applied Offload** still takes the mail out of Gmail's trash, and only while it is `OFFLOADED`.
19. **Rule chips show in every view.** The Local archive exception goes with the Local archive.
20. **Migration 0011's downgrade** renames `offload` back to `delete` in rules, versions and decisions. Mail stays offloaded, because which rows were in the Local archive is no longer known.
21. **The form's Label field** lists the mailbox's labels by name. A `label:` the mailbox doesn't have stays in "Has the words". In Deleted in Gmail the field is hidden and any `label:` stays in "Has the words", so nothing typed is lost.
22. **Size is written as `larger:5M` or `smaller:500K`** (the search box's own style). Reading takes K, KB, M or MB. Other units (G) stay in "Has the words". The age must be a whole number, and the size may have decimals.
23. **The Playwright "Delete forever in Deleted in Gmail" step** needs a mail deleted in Gmail. The fake Gmail gets a small command-line tool to delete a mail by subject, and the server reads the fake mailbox again when its file changed. Like the trash checks, the step needs `E2E_CONTAINER` or `E2E_FAKE_GMAIL`: in CI it fails without them, and locally it is skipped.
24. **Drafting** asks the model for `offload`, and reads a `delete` answer as `offload`.
25. **The Activity line for Delete forever** says "Deleted 3 mails forever from Offloaded" (or "from Deleted in Gmail").
26. **A restore out of Gmail's trash takes Gmail's labels at once.** Without this, mail a rule offloaded would come back without `INBOX` and show in Archived until the next check. Offloads by filter or pick never changed the row's labels, so they are not affected.

## Review Focus

1. **A thread with mails in different states** (one in Gmail, one offloaded). It must show in All mail with only its Gmail mail, in Offloaded with only the offloaded one, and opening it from each view must show those same mails. Owned by Task 1 (`test_a_thread_opened_from_a_view_shows_that_views_mails`).
2. **A pick-all whose set changes between the tick and the action.** The server works the set out again, and a typed count that no longer matches is refused with the new count, never applied to a different set. Owned by Task 4 (`test_a_pick_all_is_worked_out_again_when_the_offload_starts`) and Task 5 (`test_a_count_typed_for_an_older_pick_all_is_refused`).
3. **Label counts on a mailbox with many offloaded mails.** Each label's count leaves out its offloaded and deleted mail, and the Archived count leaves out mail out of Gmail. Owned by Task 1 (`test_counts_leave_out_mail_not_in_gmail`).
4. **Search text the form can't fully read** (a quoted phrase, two `from:`, `larger:2G`, `in:archived`, a label the mailbox doesn't have). Opening the form and pressing Search must keep every term. Owned by Task 7 (`keeps what it can't read in Has the words, so nothing is lost`).
5. **Old links and old clients:** `/mail?view=archive`, `view=archive` in the API, a restore with `archive: true`, and a YAML export with `delete`. Each must land on Offloaded or Offload and never error. Owned by Task 2 (`test_the_old_archive_view_is_answered_as_offloaded`), Task 3 (`test_yaml_reads_delete_as_offload`) and Task 6 (`opens Offloaded for an old Local archive link`).

---
### Task 1: Gmail views show only mail in Gmail, and the Archived view

**Files:**
- Modify: `backend/app/services/mail/search.py` (views, `in:archived`, the Gmail-view refusals)
- Modify: `backend/app/services/mail/listing.py` (`_label_tally`, `view_counts`)
- Modify: `backend/app/api/mail_reader.py` (`thread` takes `view`)
- Modify: `backend/app/services/mail/offload.py` (`restore_targets` searches in the `kept` view)
- Create: `backend/tests/test_mail_views.py`
- Modify: `backend/tests/test_mail_listing.py`, `backend/tests/test_mail_search.py`, `backend/tests/test_mail_reader_api.py`

**Interfaces:**
- Produces (in `app.services.mail.search`):
  - `GMAIL_LABEL_VIEWS: dict[str, str]` = `{"inbox": "INBOX", "sent": "SENT", "starred": "STARRED", "important": "IMPORTANT"}`
  - `GMAIL_VIEWS: frozenset[str]` = `{"all", "archived", "suggested", "inbox", "sent", "starred", "important"}`
  - `OUT_OF_GMAIL: dict[str, tuple[MailState, ...]]` = `{"offloaded": (OFFLOADED,), "deleted": (DELETED,), "kept": (OFFLOADED, DELETED)}`
  - `archived_conditions(account_id: int) -> list` (neither `INBOX` nor `SENT`)
  - `MailQuery.in_archived: bool`
  - `query_conditions(db, account_id, q, *, view: str = "all") -> list`
  - `mail_conditions(db, account_id, *, view="all", label=None, q="", now, archive=False) -> list`. `archive` and the `"archive"` view still mean the Local archive until Task 2.
- Produces: `GET /api/mail/counts` views gain `"archived"`. `GET /api/mail/threads/{id}` takes `view` (default `"all"`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mail_views.py
"""Mail panel spec §4: Gmail views hold only mail in Gmail; Archived; the counts."""

import pytest
from sqlalchemy import select

from app.models import MailLabel, MailMessage, RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, MailState
from app.services.mail.search import SearchError, mail_conditions
from tests.test_mail_listing import NOW, _add, _page


def found(db, acc, **kw):
    conds = mail_conditions(db, acc.id, now=NOW, **kw)
    return sorted(db.scalars(select(MailMessage.provider_id).where(*conds)))


def mailbox(db, acc):
    return {
        "inbox1": _add(db, acc, "inbox1", labels=("INBOX", "Label_1")),
        "sent1": _add(db, acc, "sent1", labels=("SENT",)),
        "arch1": _add(db, acc, "arch1", labels=("Label_1",)),
        "arch2": _add(db, acc, "arch2", labels=()),
        "off1": _add(db, acc, "off1", labels=("INBOX", "Label_1"), state=MailState.OFFLOADED),
        "del1": _add(db, acc, "del1", labels=("Label_1",), state=MailState.DELETED),
    }


def test_gmail_views_show_only_mail_in_gmail(db, make_account):
    acc = make_account()
    mailbox(db, acc)
    assert found(db, acc) == ["arch1", "arch2", "inbox1", "sent1"]
    assert found(db, acc, view="inbox") == ["inbox1"]
    assert found(db, acc, label="Label_1") == ["arch1", "inbox1"]
    assert found(db, acc, q="subject") == ["arch1", "arch2", "inbox1", "sent1"]
    assert found(db, acc, view="offloaded") == ["off1"]
    assert found(db, acc, view="deleted") == ["del1"]
    assert found(db, acc, view="offloaded", label="Label_1") == ["off1"]
    assert found(db, acc, view="kept") == ["del1", "off1"]


def test_archived_is_mail_in_gmail_outside_inbox_and_sent(db, make_account):
    acc = make_account()
    mailbox(db, acc)
    _add(db, acc, "both", labels=("INBOX", "SENT"))
    assert found(db, acc, view="archived") == ["arch1", "arch2"]
    assert found(db, acc, q="in:archived") == ["arch1", "arch2"]
    assert found(db, acc, view="archived", label="Label_1") == ["arch1"]
    assert found(db, acc, view="inbox", q="in:archived") == []
    assert found(db, acc, view="offloaded", q="in:archived") == []
    with pytest.raises(SearchError, match=r'"in:" only knows in:archived\.'):
        found(db, acc, q="in:spam")


def test_a_thread_shows_in_archived_when_one_of_its_mails_is(auth_client, db, make_account):
    acc = make_account()
    _add(db, acc, "t1a", thread="t1", days=3, labels=("Label_1",))
    _add(db, acc, "t1b", thread="t1", days=1, labels=("INBOX",))
    _add(db, acc, "t2a", thread="t2", days=2, labels=("INBOX",))
    rows = _page(auth_client, acc, view="archived")["threads"]
    assert [(r["thread_id"], r["count"]) for r in rows] == [("t1", 1)]
    inbox = _page(auth_client, acc, view="inbox")["threads"]
    assert [r["thread_id"] for r in inbox] == ["t1", "t2"]


@pytest.mark.parametrize("view", ["all", "inbox", "archived", "suggested"])
def test_is_offloaded_and_is_deleted_point_to_their_views(db, make_account, view):
    acc = make_account()
    with pytest.raises(SearchError, match=r"^Offloaded mail is in the Offloaded view\.$"):
        found(db, acc, view=view, q="is:offloaded")
    with pytest.raises(SearchError, match=r"^Mail deleted in Gmail is in the Deleted in Gmail view\.$"):
        found(db, acc, view=view, q="is:deleted")


def test_is_offloaded_and_is_deleted_still_work_out_of_gmail(db, make_account):
    acc = make_account()
    mailbox(db, acc)
    assert found(db, acc, view="offloaded", q="is:offloaded") == ["off1"]
    assert found(db, acc, view="kept", q="is:deleted") == ["del1"]


def test_counts_leave_out_mail_not_in_gmail(auth_client, db, make_account):
    acc = make_account()
    db.add(MailLabel(account_id=acc.id, provider_label_id="Label_1", name="Work", type="user"))
    db.commit()
    rows = mailbox(db, acc)
    for mid in ("inbox1", "off1"):
        db.add(
            RuleDecision(
                account_id=acc.id,
                message_id=rows[mid].id,
                how=DecisionHow.AI,
                status=DecisionStatus.SUGGESTED,
            )
        )
    db.commit()
    body = auth_client.get("/api/mail/counts", params={"account_id": acc.id}).json()
    views = body["views"]
    assert (views["all"], views["inbox"], views["sent"], views["archived"]) == (4, 1, 1, 2)
    assert (views["offloaded"], views["deleted"], views["suggested"]) == (1, 1, 1)
    assert body["labels"] == [{"id": "Label_1", "name": "Work", "count": 2}]
    totals = auth_client.get("/api/mail/totals", params={"account_id": acc.id}).json()
    assert totals["total"] == 4
    totals = auth_client.get(
        "/api/mail/totals", params={"account_id": acc.id, "view": "archived"}
    ).json()
    assert totals["total"] == 2


def test_a_thread_opened_from_a_view_shows_that_views_mails(auth_client, db, make_account):
    acc = make_account()
    _add(db, acc, "a", thread="t", days=2, labels=("INBOX",))
    _add(db, acc, "b", thread="t", days=1, state=MailState.OFFLOADED)
    _add(db, acc, "c", thread="t", days=0, state=MailState.DELETED)

    def ids(**params):
        r = auth_client.get("/api/mail/threads/t", params={"account_id": acc.id, **params})
        return [m["provider_id"] for m in r.json()]

    assert ids() == ["a"]
    assert ids(view="inbox") == ["a"]
    assert ids(view="offloaded") == ["b"]
    assert ids(view="deleted") == ["c"]
    assert [t["thread_id"] for t in _page(auth_client, acc)["threads"]] == ["t"]
    assert [t["count"] for t in _page(auth_client, acc, view="offloaded")["threads"]] == [1]
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_mail_views.py -q`
Expected: FAIL. `view="archived"` and `view="kept"` raise "unknown view", All mail still lists `off1` and `del1`, and the counts have no `"archived"`.

- [ ] **Step 3: Change `search.py`**

Replace `VIEWS` and `IS_VALUES` at the top with:

```python
GMAIL_LABEL_VIEWS = {
    "inbox": "INBOX",
    "sent": "SENT",
    "starred": "STARRED",
    "important": "IMPORTANT",
}
# Views of mail that is in Gmail (mail panel spec §4): they only ever show state IN_MAIL.
GMAIL_VIEWS = frozenset({"all", "archived", "suggested", *GMAIL_LABEL_VIEWS})
# Views of mail CleanSync keeps that is out of Gmail. "kept" is both, for Restore to Gmail.
OUT_OF_GMAIL = {
    "offloaded": (MailState.OFFLOADED,),
    "deleted": (MailState.DELETED,),
    "kept": (MailState.OFFLOADED, MailState.DELETED),
}
VIEWS = GMAIL_VIEWS | set(OUT_OF_GMAIL) | {"archive"}  # "archive": the Local archive, until 0011
IS_VALUES = ("unread", "starred", "offloaded", "deleted", "archived")
OFFLOADED_ELSEWHERE_MSG = "Offloaded mail is in the Offloaded view."
DELETED_ELSEWHERE_MSG = "Mail deleted in Gmail is in the Deleted in Gmail view."
```

Add `"in"` to `OPERATORS`, and add `in_archived: bool = False` to `MailQuery` (after `archived`). In `parse_query`, before the final `else:  # is:` branch, add:

```python
        elif op == "in":
            if value.lower() != "archived":
                raise SearchError('"in:" only knows in:archived.')
            q.in_archived = True
```

Add `not_` to the `sqlalchemy` import, and add this function after `has_label`:

```python
def archived_conditions(account_id: int) -> list:
    """Gmail's archive (mail panel spec §4): neither in the Inbox nor sent by the owner."""
    return [not_(has_label(account_id, "INBOX")), not_(has_label(account_id, "SENT"))]
```

Replace `query_conditions`, `view_conditions` and `mail_conditions` with:

```python
def query_conditions(db: Session, account_id: int, q: MailQuery, *, view: str = "all") -> list:
    conds: list = []
    if q.archived and view != "archive":  # inside the archive the view already limits to it
        raise SearchError("Archived mail is on the Local archive page.")
    if view in GMAIL_VIEWS:
        if q.offloaded:
            raise SearchError(OFFLOADED_ELSEWHERE_MSG)
        if q.deleted:
            raise SearchError(DELETED_ELSEWHERE_MSG)
    for s in q.senders:
        conds.append(MailMessage.from_addr.ilike(f"%{_like(s)}%", escape="\\"))
    for r in q.recipients:
        pattern = f"%{_like(r)}%"
        conds.append(
            or_(
                MailMessage.to_addrs.ilike(pattern, escape="\\"),
                MailMessage.cc_addrs.ilike(pattern, escape="\\"),
            )
        )
    for name in q.labels:
        ids = label_ids(db, account_id, name)
        conds.append(or_(*[has_label(account_id, i) for i in ids]) if ids else false())
    if q.has_attachment:
        conds.append(MailMessage.has_attachments.is_(True))
    if q.larger is not None:
        conds.append(MailMessage.size > q.larger)
    if q.smaller is not None:
        conds.append(MailMessage.size < q.smaller)
    if q.before is not None:
        conds.append(MailMessage.date < q.before)
    if q.after is not None:
        conds.append(MailMessage.date >= q.after)
    if q.unread:
        conds.append(MailMessage.unread.is_(True))
    if q.starred:
        conds.append(MailMessage.starred.is_(True))
    if q.offloaded:
        conds.append(MailMessage.state == MailState.OFFLOADED)
    if q.deleted:
        conds.append(MailMessage.state == MailState.DELETED)
    if q.in_archived:
        conds += [MailMessage.state == MailState.IN_MAIL, *archived_conditions(account_id)]
    if q.words:
        matches = (
            text("SELECT rowid FROM mail_fts WHERE mail_fts MATCH :fts")
            .bindparams(fts=fts_match(q.words))
            .columns(column("rowid"))
        )
        conds.append(MailMessage.id.in_(matches))
    return conds


def view_conditions(account_id: int, view: str, label: str | None) -> list:
    if view not in VIEWS:
        raise SearchError(f"unknown view {view!r}")
    conds: list = []
    if view in GMAIL_VIEWS:
        conds.append(MailMessage.state == MailState.IN_MAIL)
    elif view in OUT_OF_GMAIL:
        conds.append(MailMessage.state.in_(OUT_OF_GMAIL[view]))
    if view in GMAIL_LABEL_VIEWS:
        conds.append(has_label(account_id, GMAIL_LABEL_VIEWS[view]))
    elif view == "archived":
        conds += archived_conditions(account_id)
    elif view == "suggested":
        conds.append(
            MailMessage.id.in_(
                select(RuleDecision.message_id).where(
                    RuleDecision.status == DecisionStatus.SUGGESTED
                )
            )
        )
    if label:
        conds.append(has_label(account_id, label))
    return conds


def mail_conditions(
    db: Session,
    account_id: int,
    *,
    view: str = "all",
    label: str | None = None,
    q: str = "",
    now: datetime,
    archive: bool = False,
) -> list:
    """Every condition for one account, view (or label) and search, ready for .where(*…).
    Gmail views hold only mail in Gmail; the Local archive only with archive=True or the
    archive view (until migration 0011 merges it into Offloaded)."""
    if archive:
        view = "archive"
    return [
        MailMessage.account_id == account_id,
        *([MailMessage.state == MailState.LOCAL_ARCHIVE] if view == "archive" else []),
        *view_conditions(account_id, view, label),
        *query_conditions(db, account_id, parse_query(q, now=now), view=view),
    ]
```

- [ ] **Step 4: Change the counts in `listing.py`**

Import `archived_conditions` from `app.services.mail.search` (next to `SearchError`). Replace `_label_tally` and `view_counts` with:

```python
def _label_tally(db: Session, account_id: int) -> dict[str, int]:
    """Mails per label, counting only mail in Gmail (mail panel spec §4). The label table
    counts every mail kept here, so the ones out of Gmail are taken off again."""
    rows = db.execute(
        select(MailMessageLabel.label_id, func.count())
        .where(MailMessageLabel.account_id == account_id)
        .group_by(MailMessageLabel.label_id)
    ).all()
    tally = {str(v): int(n) for v, n in rows}
    gone = db.execute(
        select(MailMessageLabel.label_id, func.count())
        .join(MailMessage, MailMessage.id == MailMessageLabel.message_id)
        .where(
            MailMessage.account_id == account_id,
            MailMessage.state != MailState.IN_MAIL,
        )
        .group_by(MailMessageLabel.label_id)
    ).all()
    for v, n in gone:
        left = tally.get(str(v), 0) - int(n)
        if left > 0:
            tally[str(v)] = left
        else:
            tally.pop(str(v), None)
    return tally


def view_counts(
    db: Session, account_id: int, tally: dict[str, int] | None = None
) -> dict[str, int]:
    tally = _label_tally(db, account_id) if tally is None else tally
    states = dict(
        db.execute(
            select(MailMessage.state, func.count())
            .where(MailMessage.account_id == account_id)
            .group_by(MailMessage.state)
        ).all()
    )
    archived = (
        db.scalar(
            select(func.count())
            .select_from(MailMessage)
            .where(
                MailMessage.account_id == account_id,
                MailMessage.state == MailState.IN_MAIL,
                *archived_conditions(account_id),
            )
        )
        or 0
    )
    suggested = (
        db.scalar(
            select(func.count(func.distinct(RuleDecision.message_id)))
            .join(MailMessage, MailMessage.id == RuleDecision.message_id)
            .where(
                RuleDecision.account_id == account_id,
                RuleDecision.status == DecisionStatus.SUGGESTED,
                MailMessage.state == MailState.IN_MAIL,
            )
        )
        or 0
    )
    return {
        "all": states.get(MailState.IN_MAIL, 0),
        "inbox": tally.get("INBOX", 0),
        "sent": tally.get("SENT", 0),
        "starred": tally.get("STARRED", 0),
        "important": tally.get("IMPORTANT", 0),
        "archived": archived,
        "offloaded": states.get(MailState.OFFLOADED, 0),
        "deleted": states.get(MailState.DELETED, 0),
        "archive": states.get(MailState.LOCAL_ARCHIVE, 0),
        "suggested": suggested,
    }
```

- [ ] **Step 5: The thread endpoint takes the view**

In `backend/app/api/mail_reader.py`, add after `MAX_QUERY_CHARS`:

```python
# Opening a thread shows its mails of the view it was opened from (mail panel spec §4).
THREAD_STATES = {
    "offloaded": (MailState.OFFLOADED,),
    "deleted": (MailState.DELETED,),
    "archive": (MailState.LOCAL_ARCHIVE,),
}
```

In `thread(...)`, add the parameter `view: str = "all",` before `archive: bool = False,`. Replace the state condition in the query with `MailMessage.state.in_(states)` and set, before the query:

```python
    states = THREAD_STATES.get("archive" if archive else view, (MailState.IN_MAIL,))
```

- [ ] **Step 6: Restore searches in the `kept` view**

In `backend/app/services/mail/offload.py`, in `restore_targets`, replace the `mail_conditions(...)` call with:

```python
            *mail_conditions(
                db,
                account_id,
                view="archive" if pick.archive else "kept",
                q=(pick.q or "").strip(),
                now=now,
            )
```

- [ ] **Step 7: Update the tests that expected mail out of Gmail in Gmail views**

- `backend/tests/test_mail_listing.py`, `test_threads_are_grouped_newest_first_with_their_marks`: replace everything from `page = _page(auth_client, acc)` to the end of the test with:

```python
    page = _page(auth_client, acc)
    assert [t["thread_id"] for t in page["threads"]] == ["t1"]
    t1 = page["threads"][0]
    assert (
        t1["count"] == 2
        and t1["subject"] == "Re: Project plan"
        and t1["from_addr"] == "me@example.com"
    )
    assert t1["unread"] is True and t1["snippet"] == "snippet a2"
    assert page["next_cursor"] is None
    off = _page(auth_client, acc, view="offloaded")["threads"]
    gone = _page(auth_client, acc, view="deleted")["threads"]
    assert [t["thread_id"] for t in off] == ["t2"] and off[0]["offloaded"] is True
    assert [t["thread_id"] for t in gone] == ["t3"] and gone[0]["deleted"] is True
    totals = auth_client.get("/api/mail/totals", params={"account_id": acc.id}).json()
    assert totals == {"total": 2, "total_bytes": 2000}
    inbox = _page(auth_client, acc, view="inbox")
    assert [t["thread_id"] for t in inbox["threads"]] == ["t1"]
    assert inbox["threads"][0]["count"] == 1  # only the mail in the inbox counts
```

- Same file, `test_counts_for_views_and_labels`: the expected `views` become `{"all": 2, "inbox": 1, "sent": 1, "starred": 1, "important": 0, "archived": 0, "offloaded": 1, "deleted": 1, "archive": 0, "suggested": 0}`. The labels stay as they are.
- `backend/tests/test_mail_search.py`, the test that adds `m1` to `m4`: change these lines to

```python
    assert _found(db, acc, q="label:receipts") == ["m1"]
    assert _found(db, acc, view="offloaded", q="label:Label_2 older_than:1y") == ["m3"]
    assert _found(db, acc, q="smaller:5M") == ["m2"]
    assert _found(db, acc, view="offloaded", q="is:offloaded") == ["m3"]
    assert _found(db, acc, view="offloaded") == ["m3"]
    assert _found(db, acc, view="deleted", q="is:deleted") == ["m4"]
    assert _found(db, acc, view="deleted") == ["m4"]
    assert _found(db, acc, view="sent") == []
    assert _found(db, acc, view="all", label="Label_2") == ["m1"]
```

  (If `_found` takes no `view` keyword, pass it through to `mail_conditions` the way it passes `q`.)
- `backend/tests/test_mail_reader_api.py`, `test_a_thread_lists_its_mails_oldest_first`: replace its first two asserts with

```python
    rows = auth_client.get("/api/mail/threads/t1", params={"account_id": acc.id}).json()
    assert [r["subject"] for r in rows] == ["Plan"]
    off = auth_client.get(
        "/api/mail/threads/t1", params={"account_id": acc.id, "view": "offloaded"}
    ).json()
    assert [r["subject"] for r in off] == ["Re: Plan"]
    assert off[0]["state"] == "offloaded" and off[0]["gmail_url"] is None
```

- [ ] **Step 8: Run the mail and rules tests**

Run: `cd backend && uv run pytest tests/test_mail_views.py tests/test_mail_listing.py tests/test_mail_search.py tests/test_mail_reader_api.py tests/test_mail_local_archive.py tests/test_mail_archive.py tests/test_mail_restore.py tests/test_rules_apply.py -q`
Expected: PASS. If another assertion in these files expected offloaded or deleted mail in a Gmail view, move it to the matching view (`offloaded`, `deleted`), as in Step 7.

- [ ] **Step 9: Run the full backend suite and lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/mail/search.py backend/app/services/mail/listing.py backend/app/api/mail_reader.py backend/app/services/mail/offload.py backend/tests/test_mail_views.py backend/tests/test_mail_listing.py backend/tests/test_mail_search.py backend/tests/test_mail_reader_api.py
git commit -m "feat(mail): Gmail views show only mail in Gmail; add the Archived view and in:archived

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Migration 0011 merges the Local archive into Offloaded

**Files:**
- Create: `backend/app/database/migrations/versions/0011_offloaded_and_offload_action.py`
- Modify: `backend/app/models/enums.py` (comment on `LOCAL_ARCHIVE`)
- Modify: `backend/app/services/mail/search.py` (the `archive` view becomes an old name of `offloaded`; `is:archived` goes)
- Modify: `backend/app/services/mail/listing.py` (no `"archive"` count)
- Modify: `backend/app/api/mail_reader.py` (no `archive` parameter)
- Modify: `backend/app/services/rules/apply.py` (a trash ends `OFFLOADED`; undo needs `OFFLOADED`)
- Modify: `backend/app/services/mail/index.py` (`apply_labels`, `adopt_restored_copy`, `mark_deleted`)
- Modify: `backend/app/services/mail/offload.py` (`restore_targets`, `_restore_done`)
- Modify: `backend/app/services/mail/offload_plan.py` (`Pick.archive` goes)
- Modify: `backend/app/schemas/mail.py`, `backend/app/api/mail_offload.py` (`RestoreMailIn.archive` goes)
- Modify: `backend/app/services/mail/archive.py` (Delete forever looks at Offloaded until Task 5)
- Create: `backend/tests/test_migration_0011.py`
- Modify: `backend/tests/test_database.py`, `backend/tests/test_rules_apply.py`, `backend/tests/test_mail_archive.py`, `backend/tests/test_mail_archive_api.py`, `backend/tests/test_mail_views.py`
- Delete: `backend/tests/test_mail_local_archive.py` (its views and counts are covered by `test_mail_views.py`)

**Interfaces:**
- Consumes: Task 1's `OUT_OF_GMAIL`, `GMAIL_VIEWS`, `mail_conditions`.
- Produces:
  - `VIEW_ALIASES = {"archive": "offloaded"}` in `search.py`. `mail_conditions(db, account_id, *, view="all", label=None, q="", now)` has no `archive` keyword any more.
  - `Pick(q=None, message_ids=None, thread_ids=None)`, with no `archive` field.
  - Migration `0011` with helper `_rename_actions(conn, old: str, new: str) -> None`, which is empty in this task and filled in by Task 3.
  - A rule's trash (kind `delete` until Task 3) leaves the mail `OFFLOADED` with `offload_id = None`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_migration_0011.py
"""Migration 0011: one state for mail removed from Gmail (mail panel spec §8)."""

import json

from sqlalchemy import text

from app.database.engine import make_engine
from app.database.migrate import run_migrations

STAMP = "2026-01-02 03:04:05.000000"


def old_database(tmp_path):
    """A 0.4.0 database: one mail in the Local archive, one in Gmail, one offloaded, and a
    rule, a rule version and a decision that use the old action name."""
    f = tmp_path / "v040.db"
    run_migrations(f, "0010")
    eng = make_engine(f)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO accounts (id, email, remote_name, status, connected_at) "
                "VALUES (1, 'a@x.com', 'a', 'CONNECTED', :t)"
            ),
            {"t": STAMP},
        )
        for i, state in ((1, "local_archive"), (2, "in_mail"), (3, "offloaded")):
            c.execute(
                text(
                    "INSERT INTO mail_messages (id, account_id, provider_id, thread_id, "
                    "from_addr, to_addrs, cc_addrs, subject, date, size, file_size, sha256, "
                    "labels, unread, starred, has_attachments, attachments, snippet, "
                    "file_path, state, first_seen_at, updated_at) VALUES (:i, 1, :p, :p, '', "
                    "'', '', '', :t, 10, 12, :sha, '[]', 0, 0, 0, '[]', '', 'x', :s, :t, :t)"
                ),
                {"i": i, "p": f"m{i}", "t": STAMP, "sha": "0" * 64, "s": state},
            )
        c.execute(
            text(
                "INSERT INTO rules (id, account_id, name, description, conditions, combine, "
                "actions, mode, position, enabled, version, created_at, updated_at) VALUES "
                "(1, 1, 'Junk', 'junk mail', '{}', 'ai_only', :a, 'suggest', 0, 1, 1, :t, :t)"
            ),
            {"a": json.dumps([{"kind": "delete"}]), "t": STAMP},
        )
        c.execute(
            text(
                "INSERT INTO rules (id, account_id, name, description, conditions, combine, "
                "actions, mode, position, enabled, version, created_at, updated_at) VALUES "
                "(2, 1, 'News', 'digests', '{}', 'ai_only', :a, 'suggest', 1, 1, 1, :t, :t)"
            ),
            {
                "a": json.dumps([{"kind": "label", "label": "delete"}, {"kind": "archive"}]),
                "t": STAMP,
            },
        )
        c.execute(
            text(
                "INSERT INTO rule_versions (id, rule_id, version, snapshot, saved_by, saved_at) "
                "VALUES (1, 1, 1, :s, 'admin', :t)"
            ),
            {"s": json.dumps({"name": "Junk", "actions": [{"kind": "delete"}]}), "t": STAMP},
        )
        c.execute(
            text(
                "INSERT INTO rule_decisions (id, account_id, message_id, rule_id, rule_version, "
                "rule_name, how, why, model, took_ms, actions, changes, labels_seen, status, "
                "created_at) VALUES (1, 1, 1, NULL, NULL, '', 'manual', 'Deleted by you.', '', "
                "0, :a, :c, '[]', 'applied', :t)"
            ),
            {
                "a": json.dumps([{"kind": "delete"}]),
                "c": json.dumps({"add": [], "remove": [], "trash": True}),
                "t": STAMP,
            },
        )
    eng.dispose()
    return f


def read(f, sql):
    eng = make_engine(f)
    try:
        with eng.begin() as c:
            return c.execute(text(sql)).all()
    finally:
        eng.dispose()


def test_local_archive_mail_becomes_offloaded(tmp_path):
    f = old_database(tmp_path)
    run_migrations(f)
    assert read(f, "SELECT version_num FROM alembic_version") == [("0011",)]
    assert read(f, "SELECT id, state, offload_id FROM mail_messages ORDER BY id") == [
        (1, "offloaded", None),
        (2, "in_mail", None),
        (3, "offloaded", None),
    ]
```

In `backend/tests/test_mail_views.py`, add:

```python
def test_the_old_archive_view_is_answered_as_offloaded(auth_client, db, make_account):
    acc = make_account()
    mailbox(db, acc)
    assert found(db, acc, view="archive") == ["off1"]
    assert [t["thread_id"] for t in _page(auth_client, acc, view="archive")["threads"]] == [
        "off1"
    ]
    r = auth_client.get("/api/mail/threads/off1", params={"account_id": acc.id, "view": "archive"})
    assert [m["provider_id"] for m in r.json()] == ["off1"]
    assert "archive" not in auth_client.get(
        "/api/mail/counts", params={"account_id": acc.id}
    ).json()["views"]


def test_is_archived_is_no_longer_a_search_word(db, make_account):
    acc = make_account()
    with pytest.raises(SearchError, match='"is:" knows unread, starred, offloaded and deleted.'):
        found(db, acc, view="offloaded", q="is:archived")
```

In `backend/tests/test_rules_apply.py`:
- In `test_delete_checks_copy_trashes_and_archives_locally`, the end state becomes `MailState.OFFLOADED`. Add `assert get(gbox, MailMessage, row.id).offload_id is None`. Rename the test to `test_offload_checks_copy_trashes_and_ends_offloaded`.
- In `test_delete_by_hand_is_logged_and_undoable`, the last assert compares with `"offloaded"`.
- In `test_rule_archive_and_delete_keep_label_views_and_counts_right`, change `c["archive"] == 0` to `c["offloaded"] == 0` and `c["archive"] == 1` to `c["offloaded"] == 1`.

In `backend/tests/test_database.py`, `test_a_v0_2_1_database_upgrades_to_ai_rules`: the heads become `["0011"]`. Add `assert script.get_revision("0011").down_revision == "0010"`, and the version after `run_migrations(f)` becomes `"0011"`.

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_migration_0011.py tests/test_mail_views.py tests/test_rules_apply.py tests/test_database.py -q`
Expected: FAIL. There is no revision 0011, the archive view still reads the Local archive, and the trash ends `local_archive`.

- [ ] **Step 3: Write the migration**

```python
# backend/app/database/migrations/versions/0011_offloaded_and_offload_action.py
"""One state for mail removed from Gmail (Offloaded), and the rule action Offload

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _rename_actions(conn, old: str, new: str) -> None:
    """Task 3 fills this in: the action kind `old` becomes `new` in rules, their versions
    and decisions."""


def upgrade() -> None:
    conn = op.get_bind()
    # The Local archive was "removed from Gmail, copy kept": that is Offloaded now. These
    # mails belong to no offload batch, so offload_id stays empty.
    conn.execute(sa.text("UPDATE mail_messages SET state = 'offloaded' WHERE state = 'local_archive'"))
    _rename_actions(conn, "delete", "offload")


def downgrade() -> None:
    # Mail stays offloaded: which rows were in the Local archive isn't known any more.
    _rename_actions(op.get_bind(), "offload", "delete")
```

Task 3 adds `import json` when it fills in `_rename_actions`.

- [ ] **Step 4: Merge the state in the code**

1. `backend/app/models/enums.py`: replace the two comment lines above `LOCAL_ARCHIVE` with:

```python
    # No longer used: migration 0011 moved it into OFFLOADED. Kept so old backups still read.
```

2. `backend/app/services/mail/search.py`:
   - `VIEWS = GMAIL_VIEWS | set(OUT_OF_GMAIL)`. Add `VIEW_ALIASES = {"archive": "offloaded"}  # old links to the Local archive`.
   - `IS_VALUES = ("unread", "starred", "offloaded", "deleted")`. Remove `archived` from `MailQuery`, and remove the `q.archived` check from `query_conditions`.
   - `mail_conditions` loses `archive` and becomes:

```python
def mail_conditions(
    db: Session,
    account_id: int,
    *,
    view: str = "all",
    label: str | None = None,
    q: str = "",
    now: datetime,
) -> list:
    """Every condition for one account, view (or label) and search, ready for .where(*…).
    Gmail views hold only mail in Gmail (mail panel spec §4)."""
    view = VIEW_ALIASES.get(view, view)
    return [
        MailMessage.account_id == account_id,
        *view_conditions(account_id, view, label),
        *query_conditions(db, account_id, parse_query(q, now=now), view=view),
    ]
```

3. `backend/app/services/mail/listing.py`: delete the `"archive": ...` line from `view_counts`. In `decision_chips`, `"Deleted by you"` becomes `"Offloaded by you"`.

4. `backend/app/api/mail_reader.py`: remove `"archive"` from `THREAD_STATES`, remove the `archive: bool = False` parameter from `thread`, import `VIEW_ALIASES` from `app.services.mail.search`, and use:

```python
    states = THREAD_STATES.get(VIEW_ALIASES.get(view, view), (MailState.IN_MAIL,))
```

5. `backend/app/services/rules/apply.py`:
   - The module docstring's last sentence becomes "Offload checks the local copy, moves the mail to Gmail's trash and marks it offloaded."
   - In `apply`, `row.state = MailState.LOCAL_ARCHIVE` becomes:

```python
            if planned.trash:
                row.state, row.offload_id = MailState.OFFLOADED, None  # in no offload batch
```

   - In `undo`, the comment and check become:

```python
            # An offload is undone while it's still offloaded; a label change while in Gmail.
            if state != (MailState.OFFLOADED if changes.get("trash") else MailState.IN_MAIL):
```

6. `backend/app/services/mail/index.py`:
   - In `apply_labels`, delete the two lines `if row.state == MailState.LOCAL_ARCHIVE and gone: return row ...`. An offloaded mail in Gmail's trash is already handled by the `OFFLOADED` branch.
   - In `adopt_restored_copy`, the state list becomes `[MailState.OFFLOADED, MailState.DELETED]`.
   - The `mark_deleted` docstring drops ", and local-archive mail stays in the archive".

7. `backend/app/services/mail/offload_plan.py`: remove `archive: bool = False` from `Pick`.

8. `backend/app/services/mail/offload.py`: replace `restore_targets` with the version below (Task 4 adds the view), and in `_restore_done` the state list becomes `[MailState.OFFLOADED, MailState.DELETED]`.

```python
def restore_targets(
    db: Session, account_id: int, pick: Pick, offload_id: int | None, now: datetime
) -> list[int]:
    """The offloaded and Deleted-in-Gmail mails of a pick (or of one offload): the only ones
    a restore touches. Mail already in Gmail is never part of it."""
    restorable = (MailState.OFFLOADED, MailState.DELETED)
    if offload_id is None and (pick.message_ids or pick.thread_ids):
        # A picked list can be longer than SQLite takes in one query: asked in chunks.
        column = MailMessage.id if pick.message_ids else MailMessage.thread_id
        rows = rows_where_in(db, account_id, column, pick.message_ids or pick.thread_ids or [])
        found = {r.id: r for r in rows if r.state in restorable}
        return [r.id for r in sorted(found.values(), key=lambda r: (as_utc(r.date), r.id))]
    q = select(MailMessage.id).where(
        MailMessage.account_id == account_id,
        MailMessage.state.in_(restorable),
    )
    if offload_id is not None:
        q = q.where(MailMessage.offload_id == offload_id)
    elif (pick.q or "").strip():
        q = q.where(
            *mail_conditions(db, account_id, view="kept", q=(pick.q or "").strip(), now=now)
        )
    else:
        return []
    return list(db.scalars(q.order_by(MailMessage.date, MailMessage.id)))
```

9. `backend/app/schemas/mail.py`: remove `archive: bool = False` from `RestoreMailIn`. In `backend/app/api/mail_offload.py`, `restore` calls `request_restore(body.account_id, _pick(body), body.offload_id)`, and the `replace` import goes. An old client that still sends `archive` is fine, because Pydantic ignores fields it doesn't know.

10. `backend/app/services/mail/archive.py`: until Task 5 replaces this module, Delete forever works on Offloaded. In `archive_targets`, `r.state == MailState.LOCAL_ARCHIVE` becomes `r.state == MailState.OFFLOADED`, and the `mail_conditions(...)` call becomes `mail_conditions(db, account_id, view="offloaded", q=q, now=now)`.

- [ ] **Step 5: Update the tests that named the Local archive**

- `git rm backend/tests/test_mail_local_archive.py`
- `backend/tests/test_mail_archive.py`:
  - In `archived(...)`, set `row.state = MailState.OFFLOADED`.
  - In `_archive(...)`, set `row.state, row.offload_id = MailState.OFFLOADED, None`.
  - Remove every `archive=True` argument (the `Pick(...)` and `_restore(...)` calls).
  - In `test_nothing_in_the_archive_is_refused`, the `match=` stays `"local archive"`: the message is unchanged until Task 5.
- `backend/tests/test_mail_archive_api.py`:
  - Replace `"view": "archive"` with `"view": "offloaded"`, and `{"account_id": acc.id, "archive": True}` with `{"account_id": acc.id, "view": "offloaded"}`.
  - Replace `"local_archive"` with `"offloaded"`.

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest tests/test_migration_0011.py tests/test_mail_views.py tests/test_rules_apply.py tests/test_database.py tests/test_mail_archive.py tests/test_mail_archive_api.py tests/test_mail_restore.py tests/test_mail_keep_up.py -q`
Expected: PASS.

- [ ] **Step 7: Check nothing else uses the old state**

Run: `cd backend && grep -rn "LOCAL_ARCHIVE\|local_archive" app --include=*.py`
Expected: only the enum line in `app/models/enums.py` and the `UPDATE` in migration 0011.

- [ ] **Step 8: Run the full backend suite and lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add -A backend/app backend/tests
git commit -m "feat(mail): migration 0011 merges the Local archive into Offloaded

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 3: The Offload rule action, and Offload on one mail

**Files:**
- Modify: `backend/app/database/migrations/versions/0011_offloaded_and_offload_action.py` (fill in `_rename_actions`)
- Modify: `backend/app/services/rules/store.py` (action kinds, `delete_forever` refused, YAML reads `delete`)
- Modify: `backend/app/services/rules/apply.py` (`plan_changes`, `offload_by_hand`)
- Modify: `backend/app/services/rules/drafting.py` (prompt and old name)
- Modify: `backend/app/schemas/mail.py` (`OffloadOneOut`)
- Modify: `backend/app/api/mail_offload.py` (new `POST /api/mail/messages/{message_id}/offload`)
- Modify: `backend/app/api/mail_archive.py` (remove `POST /api/mail/delete`)
- Create: `backend/tests/test_mail_offloaded_state.py`
- Modify: `backend/tests/test_migration_0011.py`, `backend/tests/test_rules_store.py`, `backend/tests/test_rules_apply.py`, `backend/tests/test_rules_drafting.py`, `backend/tests/test_mail_archive_api.py`

**Interfaces:**
- Consumes: Task 2's `_rename_actions(conn, old, new)` stub, and the applier ending a trash in `OFFLOADED`.
- Produces:
  - `store.ACTION_KINDS = ("archive", "label", "mark_read", "star", "offload")`
  - `store.NOT_YET_KINDS = {"delete_forever": "Delete forever needs full Gmail access (coming in this release)."}`
  - `store.action_kind(kind: str) -> str` (`"delete"` gives `"offload"`, anything else is unchanged)
  - `Applier.offload_by_hand(account_id: int, message_id: int) -> RuleDecision` (replaces `delete_by_hand`)
  - `POST /api/mail/messages/{message_id}/offload` with body `{account_id}` gives `{"offloaded": bool, "reason": str | None}`: 404 for an unknown mail, 503 while Google makes it wait.
  - `POST /api/mail/delete` no longer exists.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mail_offloaded_state.py
"""Mail panel spec §8: one "removed from Gmail" state. A rule's Offload and Offload on one
mail reach it through the applier; the one restore path leaves it."""

from app.models import RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, MailState
from app.services.google.quota import BudgetReached
from tests.test_mail_restore import _restore


async def test_a_rules_offload_ends_offloaded_and_restore_brings_it_back(
    auth_client, offload_box, app
):
    box = offload_box
    await box.backup()
    row = box.row("Old photos")
    with app.state.session_factory() as db:
        d = RuleDecision(
            account_id=box.acc.id,
            message_id=row.id,
            rule_name="Junk",
            how=DecisionHow.AI,
            actions=[{"kind": "offload"}],
            labels_seen=list(row.labels),
            status=DecisionStatus.SUGGESTED,
        )
        db.add(d)
        db.commit()
        did = d.id
    assert await app.state.rule_applier.apply(did) == "applied"
    after = box.row("Old photos")
    assert after.state == MailState.OFFLOADED and after.offload_id is None
    assert "TRASH" in box.fake.messages[row.provider_id].labels
    listed = auth_client.get(
        "/api/mail/threads", params={"account_id": box.acc.id, "view": "offloaded"}
    ).json()["threads"]
    assert [t["subject"] for t in listed] == ["Old photos"]
    # Not part of any offload batch: the Batches tab lists only filter and pick offloads.
    assert auth_client.get("/api/mail/offloads", params={"account_id": box.acc.id}).json() == []
    assert _restore(auth_client, box, message_ids=[row.id]).status_code == 202
    await box.run_queued()
    assert box.row("Old photos").state == MailState.IN_MAIL
    assert "TRASH" not in box.fake.messages[row.provider_id].labels


async def test_offload_on_one_mail_goes_through_the_applier(auth_client, offload_box):
    box = offload_box
    await box.backup()
    row = box.row("Yesterday")  # like Delete before it, one mail by hand isn't held back by age
    url = f"/api/mail/messages/{row.id}/offload"
    r = auth_client.post(url, json={"account_id": box.acc.id})
    assert r.status_code == 200 and r.json() == {"offloaded": True, "reason": None}
    assert box.row("Yesterday").state == MailState.OFFLOADED
    again = auth_client.post(url, json={"account_id": box.acc.id})
    assert again.json() == {
        "offloaded": False,
        "reason": "It's no longer in Gmail, so nothing was changed.",
    }
    r = auth_client.post("/api/mail/messages/999999/offload", json={"account_id": box.acc.id})
    assert r.status_code == 404


async def test_offload_on_one_mail_says_when_google_makes_it_wait(auth_client, offload_box, app):
    box = offload_box
    await box.backup()

    async def wait(account_id, message_id):
        raise BudgetReached(1000)

    app.state.rule_applier.offload_by_hand = wait
    r = auth_client.post(
        f"/api/mail/messages/{box.row('Yesterday').id}/offload", json={"account_id": box.acc.id}
    )
    assert r.status_code == 503 and r.json()["detail"].endswith("Nothing was offloaded; try again later.")


def test_the_old_delete_endpoint_is_gone(auth_client, make_account):
    acc = make_account()
    r = auth_client.post("/api/mail/delete", json={"account_id": acc.id, "message_ids": [1]})
    assert r.status_code in (404, 405)
```

In `backend/tests/test_migration_0011.py`, add:

```python
from tests.test_mail_speed_schema import _downgrade


def test_the_action_delete_becomes_offload(tmp_path):
    f = old_database(tmp_path)
    run_migrations(f)
    actions = {i: json.loads(a) for i, a in read(f, "SELECT id, actions FROM rules")}
    assert actions[1] == [{"kind": "offload"}]
    assert actions[2] == [{"kind": "label", "label": "delete"}, {"kind": "archive"}]
    ((snapshot,),) = read(f, "SELECT snapshot FROM rule_versions")
    assert json.loads(snapshot) == {"name": "Junk", "actions": [{"kind": "offload"}]}
    ((acts, changes),) = read(f, "SELECT actions, changes FROM rule_decisions")
    assert json.loads(acts) == [{"kind": "offload"}]
    assert json.loads(changes)["trash"] is True


def test_downgrade_gives_the_old_action_name_back_and_keeps_mail_offloaded(tmp_path):
    f = old_database(tmp_path)
    run_migrations(f)
    _downgrade(f, "0010")
    ((acts,),) = read(f, "SELECT actions FROM rules WHERE id = 1")
    assert json.loads(acts) == [{"kind": "delete"}]
    assert read(f, "SELECT state FROM mail_messages WHERE id = 1") == [("offloaded",)]
```

In `backend/tests/test_rules_store.py`:
- In the `test_bad_rules_are_refused` parameters, the case `[{"kind": "delete"}, {"kind": "star"}]` with `"Delete"` becomes `[{"kind": "offload"}, {"kind": "star"}]` with `"Offload"`.
- Add these tests:

```python
def test_delete_forever_is_listed_but_not_yet_allowed(db, make_account):
    acc = make_account()
    data = RuleIn(name="x", description="d", actions=[{"kind": "delete_forever"}])
    with pytest.raises(
        RuleProblem, match=r"^Delete forever needs full Gmail access \(coming in this release\)\.$"
    ):
        create_rule(db, acc.id, data, "admin")


def test_the_old_name_delete_is_refused_when_saving(db, make_account):
    acc = make_account()
    with pytest.raises(RuleProblem, match="isn't an action"):
        create_rule(db, acc.id, RuleIn(name="x", description="d", actions=[{"kind": "delete"}]), "admin")


def test_yaml_reads_delete_as_offload(db, make_account):
    acc = make_account()
    text = (
        "cleansync_rules: 1\n"
        "rules:\n"
        "- name: Junk\n"
        "  description: junk mail\n"
        "  actions:\n"
        "  - kind: delete\n"
    )
    (rule,) = import_yaml(db, acc.id, text, "admin")
    assert rule.actions == [{"kind": "offload"}]
    assert "kind: offload" in export_yaml(db, acc.id)
```

In `backend/tests/test_rules_drafting.py`, add:

```python
async def test_a_delete_answer_is_read_as_offload():
    c = Client({"name": "Junk", "description": "junk mail", "actions": ["delete"]})
    d = await sentence_to_rule(c, T, "get rid of junk")
    assert d.actions == [{"kind": "offload"}] and d.notes == []
```

In `backend/tests/test_rules_apply.py`:
- Every `{"kind": "delete"}` becomes `{"kind": "offload"}`.
- `delete_by_hand` becomes `offload_by_hand`, and the test name becomes `test_offload_by_hand_is_logged_and_undoable`. In that test, add after the first call: `assert d.why == "Offloaded by you." and d.actions == [{"kind": "offload"}]`.

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_mail_offloaded_state.py tests/test_migration_0011.py tests/test_rules_store.py tests/test_rules_drafting.py tests/test_rules_apply.py -q`
Expected: FAIL. `offload` isn't an action yet, the endpoint is missing, and the migration doesn't rename.

- [ ] **Step 3: Fill in the migration**

In `0011_offloaded_and_offload_action.py`, add `import json` at the top and replace `_rename_actions` with:

```python
# Fixed statements, one pair per table (no SQL is built from strings).
_ACTION_LISTS = (
    (
        sa.text("SELECT id, actions FROM rules"),
        sa.text("UPDATE rules SET actions = :v WHERE id = :i"),
    ),
    (
        sa.text("SELECT id, actions FROM rule_decisions"),
        sa.text("UPDATE rule_decisions SET actions = :v WHERE id = :i"),
    ),
)


def _load(raw):
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None


def _renamed(actions, old: str, new: str) -> list | None:
    """The list with each action of kind `old` renamed; None when nothing changed."""
    if not isinstance(actions, list):
        return None
    out = [
        {**a, "kind": new} if isinstance(a, dict) and a.get("kind") == old else a
        for a in actions
    ]
    return out if out != actions else None


def _rename_actions(conn, old: str, new: str) -> None:
    """The action kind `old` becomes `new` in rules, their saved versions and decisions."""
    for select_, update in _ACTION_LISTS:
        for rid, raw in conn.execute(select_).all():
            out = _renamed(_load(raw), old, new)
            if out is not None:
                conn.execute(update, {"v": json.dumps(out), "i": rid})
    for rid, raw in conn.execute(sa.text("SELECT id, snapshot FROM rule_versions")).all():
        snap = _load(raw)
        out = _renamed(snap.get("actions"), old, new) if isinstance(snap, dict) else None
        if out is not None:
            conn.execute(
                sa.text("UPDATE rule_versions SET snapshot = :v WHERE id = :i"),
                {"v": json.dumps({**snap, "actions": out}), "i": rid},
            )
```

- [ ] **Step 4: The rule store**

In `backend/app/services/rules/store.py`, replace `ACTION_KINDS` with:

```python
ACTION_KINDS = ("archive", "label", "mark_read", "star", "offload")
# Listed in the rule form, but it needs the full Gmail permission (round 3 of 0.5.0).
NOT_YET_KINDS = {
    "delete_forever": "Delete forever needs full Gmail access (coming in this release).",
}
# Names older files still use: a 0.4.0 export says "delete" for Offload.
OLD_ACTION_NAMES = {"delete": "offload"}
STANDS_ALONE = ("offload",)


def action_kind(kind: str) -> str:
    return OLD_ACTION_NAMES.get(kind, kind)
```

In `clean_actions`, right after `kind = str(a.get("kind") or "")`, add:

```python
        if kind in NOT_YET_KINDS:
            raise RuleProblem(NOT_YET_KINDS[kind])
```

and replace the last check with:

```python
    if any(a["kind"] in STANDS_ALONE for a in out) and len(out) > 1:
        raise RuleProblem("Offload can't be combined with other actions.")
```

Replace `_from_item` with:

```python
def _from_item(item: dict) -> RuleIn:
    fields = {k: item[k] for k in (*SNAPSHOT, "enabled") if k in item}
    fields.setdefault("name", "")
    if isinstance(fields.get("actions"), list):  # an older file's action names
        fields["actions"] = [
            {**a, "kind": action_kind(str(a.get("kind") or ""))} if isinstance(a, dict) else a
            for a in fields["actions"]
        ]
    data = RuleIn(**fields)
    data.name = " ".join(str(data.name or "").split())
    return data
```

- [ ] **Step 5: The applier**

In `backend/app/services/rules/apply.py`, `plan_changes` reads `elif kind == "offload":` in place of `elif kind == "delete":`. Replace `delete_by_hand` with:

```python
    async def offload_by_hand(self, account_id: int, message_id: int) -> RuleDecision:
        """Offload on one mail open in the reader (mail panel spec §8): the path a rule's
        Offload takes, logged as the owner's own so it can be undone."""
        with self.sf() as db:
            row = db.get(MailMessage, message_id)
            if row is None or row.account_id != account_id:
                raise LookupError("That mail isn't here.")
            d = RuleDecision(
                account_id=account_id,
                message_id=message_id,
                how=DecisionHow.MANUAL,
                why="Offloaded by you.",
                actions=[{"kind": "offload"}],
                labels_seen=list(row.labels or []),
                status=DecisionStatus.SUGGESTED,
            )
            db.add(d)
            db.commit()
            did = d.id
        await self.apply(did)
        with self.sf() as db:
            return db.get(RuleDecision, did)
```

In `apply`, the bad-copy reason becomes `f"The copy here didn't check out ({problem}), so nothing was offloaded."`. If `test_rules_apply.py` matches `"nothing was deleted"`, change it to `"nothing was offloaded"`. In `backend/app/models/enums.py`, the `DecisionHow.MANUAL` comment becomes `# the owner's own Offload or correction, logged so it can be undone`.

- [ ] **Step 6: Drafting**

In `backend/app/services/rules/drafting.py`, import `action_kind` from `app.services.rules.store`. In `DRAFT_SYSTEM`, the action list becomes `(a list from: archive, label, mark_read, star, offload)`. The `kinds` line becomes:

```python
    kinds = list(
        dict.fromkeys(
            action_kind(str(k).strip().lower()) for k in data.get("actions") or [] if str(k).strip()
        )
    )
```

- [ ] **Step 7: The one-mail endpoint, and the old Delete removed**

In `backend/app/schemas/mail.py`, add:

```python
class OffloadOneOut(BaseModel):
    offloaded: bool
    reason: str | None = None
```

In `backend/app/api/mail_offload.py`, add the imports `from app.models.enums import DecisionStatus`, `from app.schemas.mail import AccountIn, OffloadOneOut` (into the existing import list) and `from app.services.rules.apply import WAIT_ERRORS`, then add:

```python
@router.post("/api/mail/messages/{message_id}/offload", response_model=OffloadOneOut)
async def offload_one(
    message_id: int,
    body: AccountIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Offload on one mail in the reader (mail panel spec §8): checked, moved to Gmail's
    trash, kept here as offloaded. Through the applier, like a rule's Offload."""
    account_or_404(db, body.account_id)
    try:
        d = await request.app.state.rule_applier.offload_by_hand(body.account_id, message_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except WAIT_ERRORS as e:
        raise HTTPException(503, f"{e} Nothing was offloaded; try again later.") from e
    request.app.state.bus.publish("rules", {"account_id": body.account_id})
    request.app.state.bus.publish("mail", {"account_id": body.account_id})
    return OffloadOneOut(offloaded=d.status == DecisionStatus.APPLIED, reason=d.reason)
```

In `backend/app/api/mail_archive.py`, delete `DeleteIn` and the `delete` route. Then remove the imports nothing uses any more (`select`, `MailMessage`, `MailState`, `WAIT_ERRORS`), and change the module docstring to `"""Delete forever (until Task 5 moves it to mail_forever.py)."""`.

**Restore takes Gmail's labels back.** A rule's Offload changes the row's labels to what Gmail shows after a trash (no `INBOX`, plus `TRASH`). Today a restore out of the trash leaves those labels until the next check, so the mail would show in Archived instead of the Inbox. In `backend/app/services/mail/offload.py`, import `set_flags` from `app.services.mail.index`. In `_restore_one`'s last `with self.sf() as db:` block, right after `row.import_started_at = None`, add:

```python
            if outcome == UNTRASHED:
                set_flags(row, list(st.labels))  # Gmail's labels once out of its trash
```

In `test_a_rules_offload_ends_offloaded_and_restore_brings_it_back` (Step 1), add at the end:

```python
    back = box.row("Old photos")
    assert "INBOX" in back.labels and "TRASH" not in back.labels
```

- [ ] **Step 8: Tests that used the old Delete to set up**

In `backend/tests/test_mail_archive_api.py`:
- Delete `test_delete_moves_to_archive_and_hides_from_threads`, `test_delete_by_thread_and_unknown_mail` and `test_delete_stops_politely_when_google_has_to_wait`.
- Wherever a remaining test calls `auth_client.post("/api/mail/delete", json={"account_id": acc.id, "message_ids": [row.id]})`, use `auth_client.post(f"/api/mail/messages/{row.id}/offload", json={"account_id": acc.id})` instead.

- [ ] **Step 9: Run the tests**

Run: `cd backend && uv run pytest tests/test_mail_offloaded_state.py tests/test_migration_0011.py tests/test_rules_store.py tests/test_rules_drafting.py tests/test_rules_apply.py tests/test_mail_archive_api.py tests/test_rules_api.py -q`
Expected: PASS.

- [ ] **Step 10: Run the full backend suite and lint**

Run: `cd backend && grep -rn '"delete"' app/services/rules app/api/rules.py; uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: the grep finds only `OLD_ACTION_NAMES` in `store.py`. Tests and lint PASS.

- [ ] **Step 11: Commit**

```bash
git add -A backend/app backend/tests
git commit -m "feat(rules): the Delete action is now Offload; Offload on one mail goes through the applier

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Pick-all on the server for Offload and Restore

**Files:**
- Modify: `backend/app/schemas/mail.py` (`MailPickIn` gains `view`, `label`; `RestoreMailIn.view` defaults to `"kept"`)
- Modify: `backend/app/api/mail_offload.py` (`_pick`)
- Modify: `backend/app/services/mail/offload_plan.py` (`Pick.view`, `Pick.label`, `candidates`, `describe_pick`)
- Modify: `backend/app/services/mail/offload.py` (`restore_targets` by view)
- Create: `backend/tests/test_mail_pick_all.py`

**Interfaces:**
- Consumes: Task 1's `mail_conditions(view=, label=)` and the views `kept`, `offloaded`, `deleted`.
- Produces:
  - `Pick(q=None, message_ids=None, thread_ids=None, view="all", label=None)`
  - `describe_pick(db: Session, account_id: int, pick: Pick, n: int) -> str`
  - `POST /api/mail/offloads/preview`, `POST /api/mail/offloads` and `POST /api/mail/restore` take `{account_id, view, label, q}` beside the existing `message_ids`, `thread_ids` and `offload_id`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mail_pick_all.py
"""Mail panel spec §6, §9: a pick-all travels as {view, label, q}, and the server works out
the mails when the action starts."""

from datetime import UTC, datetime

from app.devtools.fake_gmail import make_mail
from app.models.enums import MailState
from app.services.mail.offload import restore_targets
from app.services.mail.offload_plan import Pick
from tests.rules_helpers import keep
from tests.test_mail_restore import _offloaded, _restore


def _preview(client, box, **pick):
    return client.post("/api/mail/offloads/preview", json={"account_id": box.acc.id, **pick})


async def test_an_offload_from_a_pick_all_in_a_label_view(auth_client, offload_box):
    box = offload_box
    await box.backup()
    pick = {"view": "all", "label": "Label_2", "q": ""}  # Receipts
    p = _preview(auth_client, box, **pick)
    assert p.status_code == 200 and (p.json()["count"], p.json()["filter_text"]) == (1, "Receipts")
    r = auth_client.post("/api/mail/offloads", json={"account_id": box.acc.id, **pick, "confirm": 1})
    assert r.status_code == 202
    await box.run_queued()
    assert box.row("Old statement").state == MailState.OFFLOADED
    assert box.row("Old photos").state == MailState.IN_MAIL


async def test_a_pick_all_in_the_inbox_with_a_search(auth_client, offload_box):
    box = offload_box
    await box.backup()
    p = _preview(auth_client, box, view="inbox", q="older_than:1y").json()
    assert (p["count"], p["filter_text"]) == (3, "Inbox · older_than:1y")


async def test_a_pick_all_in_all_mail_needs_a_search_or_a_label(auth_client, offload_box):
    box = offload_box
    await box.backup()
    r = _preview(auth_client, box, view="all", q="")
    assert r.status_code == 422
    assert r.json()["detail"] == "Choose at least one filter, or pick mails in the list."
    assert _preview(auth_client, box, view="archived", q="").status_code == 200
    assert _preview(auth_client, box, view="nonsense", q="x").status_code == 422


async def test_a_pick_all_is_worked_out_again_when_the_offload_starts(auth_client, offload_box):
    box = offload_box
    await box.backup()
    pick = {"view": "all", "label": "Label_2", "q": ""}
    assert _preview(auth_client, box, **pick).json()["count"] == 1
    box.fake.set_labels(box.provider_id("Old photos"), ("INBOX", "Label_2"))  # labelled in Gmail
    await box.backup()
    r = auth_client.post("/api/mail/offloads", json={"account_id": box.acc.id, **pick, "confirm": 1})
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "The number of mails changed since the preview (now 2). Check the preview again."
    )
    assert box.row("Old photos").state == MailState.IN_MAIL


async def test_restore_takes_a_view_and_a_search(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)  # Old statement, Old photos, Ancient note
    assert _restore(auth_client, box, view="offloaded", q="photos").json()["count"] == 1
    assert _restore(auth_client, box, view="offloaded", q="").json()["count"] == 3
    assert _restore(auth_client, box, view="offloaded", label="Label_2", q="").json()["count"] == 1
    for view in ("deleted", "inbox"):
        r = _restore(auth_client, box, view=view, q="")
        assert r.status_code == 409
        assert r.json()["detail"] == "Nothing here needs restoring: these mails are in Gmail."


def test_a_picked_thread_restores_only_the_mails_of_its_view(db, layout, make_account):
    acc = make_account()
    a = keep(db, layout, acc, make_mail("Offloaded one"))
    b = keep(db, layout, acc, make_mail("Deleted one"))
    a.state, b.state, b.thread_id = MailState.OFFLOADED, MailState.DELETED, a.thread_id
    db.commit()
    now = datetime(2026, 10, 13, tzinfo=UTC)

    def ids(view):
        return sorted(restore_targets(db, acc.id, Pick(thread_ids=[a.thread_id], view=view), None, now))

    assert ids("offloaded") == [a.id]
    assert ids("deleted") == [b.id]
    assert ids("kept") == sorted([a.id, b.id])
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_mail_pick_all.py -q`
Expected: FAIL. `Pick` has no `view`, and the label and view are ignored.

- [ ] **Step 3: The pick**

In `backend/app/schemas/mail.py`, `MailPickIn` gains:

```python
    view: str = Field("all", max_length=32)
    label: str | None = Field(None, max_length=128)
```

and `RestoreMailIn` gains `view: str = Field("kept", max_length=32)`, so a restore without a view looks at both CleanSync views.

In `backend/app/api/mail_offload.py`:

```python
def _pick(body: MailPickIn) -> Pick:
    return Pick(
        q=body.q,
        message_ids=body.message_ids,
        thread_ids=body.thread_ids,
        view=body.view,
        label=body.label or None,
    )
```

- [ ] **Step 4: Offload works out a pick-all**

In `backend/app/services/mail/offload_plan.py`, import `MailLabel` from `app.models`. Replace `Pick` with:

```python
@dataclass
class Pick:
    """What an action works on: picked mails or threads, or a pick-all as the view, the
    label and the search (mail panel spec §6)."""

    q: str | None = None
    message_ids: list[int] | None = None
    thread_ids: list[str] | None = None
    view: str = "all"
    label: str | None = None


VIEW_NAMES = {
    "inbox": "Inbox",
    "sent": "Sent",
    "starred": "Starred",
    "important": "Important",
    "archived": "Archived",
    "suggested": "Suggested",
    "offloaded": "Offloaded",
    "deleted": "Deleted in Gmail",
}


def describe_pick(db: Session, account_id: int, pick: Pick, n: int) -> str:
    """The offload's name on its page: "12 picked mails", or the view, label and search."""
    if pick.message_ids or pick.thread_ids:
        return f"{n:,} picked {'mail' if n == 1 else 'mails'}"
    parts = [VIEW_NAMES[pick.view]] if pick.view in VIEW_NAMES else []
    if pick.label:
        name = db.scalar(
            select(MailLabel.name).where(
                MailLabel.account_id == account_id, MailLabel.provider_label_id == pick.label
            )
        )
        parts.append(name or pick.label)
    if (pick.q or "").strip():
        parts.append((pick.q or "").strip())
    return " · ".join(parts)
```

In `candidates`, replace everything from `q = (pick.q or "").strip()` to the end with:

```python
    q = (pick.q or "").strip()
    if not q and pick.view == "all" and not pick.label:
        raise SearchError(NO_FILTER_MSG)  # one tick never offloads a whole mailbox
    stmt = (
        select(MailMessage)
        .where(*mail_conditions(db, account_id, view=pick.view, label=pick.label, q=q, now=now))
        .options(load_only(*_NEEDED))
    )
    return list(db.scalars(stmt))
```

In `plan_offload`, `filter_text=pick.describe(len(rows))` becomes `filter_text=describe_pick(db, account.id, pick, len(rows))`.

- [ ] **Step 5: Restore works out a view**

In `backend/app/services/mail/offload.py`, replace `restore_targets` with:

```python
RESTORE_STATES = {"offloaded": (MailState.OFFLOADED,), "deleted": (MailState.DELETED,)}
KEPT = (MailState.OFFLOADED, MailState.DELETED)


def restore_targets(
    db: Session, account_id: int, pick: Pick, offload_id: int | None, now: datetime
) -> list[int]:
    """The mails out of Gmail of a pick, a view and search, or one offload: the only ones a
    restore touches. Mail in Gmail is never part of it. A view other than Offloaded or
    Deleted in Gmail counts as both (the "kept" view)."""
    states = RESTORE_STATES.get(pick.view, KEPT)
    view = pick.view if pick.view in RESTORE_STATES else "kept"
    if offload_id is None and (pick.message_ids or pick.thread_ids):
        # A picked list can be longer than SQLite takes in one query: asked in chunks.
        column = MailMessage.id if pick.message_ids else MailMessage.thread_id
        rows = rows_where_in(db, account_id, column, pick.message_ids or pick.thread_ids or [])
        # A picked thread can hold offloaded and deleted mail: keep to the view it was picked in.
        found = {r.id: r for r in rows if r.state in states}
        return [r.id for r in sorted(found.values(), key=lambda r: (as_utc(r.date), r.id))]
    q = select(MailMessage.id).where(
        MailMessage.account_id == account_id, MailMessage.state.in_(states)
    )
    text_q = (pick.q or "").strip()
    if offload_id is not None:
        q = q.where(MailMessage.offload_id == offload_id)
    elif view != "kept" or text_q:  # a whole CleanSync view, or a search
        q = q.where(
            *mail_conditions(db, account_id, view=view, label=pick.label, q=text_q, now=now)
        )
    else:
        return []
    return list(db.scalars(q.order_by(MailMessage.date, MailMessage.id)))
```

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest tests/test_mail_pick_all.py tests/test_mail_offload_plan.py tests/test_mail_offload_run.py tests/test_mail_restore.py tests/test_mail_archive.py tests/test_mail_offloaded_state.py -q`
Expected: PASS. If `test_mail_offload_plan.py` calls `Pick(...).describe(...)`, call `describe_pick(db, acc.id, pick, n)` there instead.

- [ ] **Step 7: Run the full backend suite and lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/schemas/mail.py backend/app/api/mail_offload.py backend/app/services/mail/offload_plan.py backend/app/services/mail/offload.py backend/tests/test_mail_pick_all.py backend/tests/test_mail_offload_plan.py
git commit -m "feat(mail): Offload and Restore take a pick-all as the view, label and search

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Delete forever in Offloaded and Deleted in Gmail

**Files:**
- Create: `backend/app/services/mail/forever.py`
- Create: `backend/app/api/mail_forever.py`
- Delete: `backend/app/services/mail/archive.py`, `backend/app/api/mail_archive.py`
- Modify: `backend/app/main.py` (router), `backend/app/api/rules.py` (`ArchiveProblem` becomes `ForeverProblem`)
- Create: `backend/tests/test_mail_forever.py`
- Modify: `backend/tests/test_mail_offloaded_state.py` (gets the restore and chip tests worth keeping)
- Delete: `backend/tests/test_mail_archive.py`, `backend/tests/test_mail_archive_api.py`

**Interfaces:**
- Consumes: `VIEW_ALIASES`, `mail_conditions` (Task 2), `rows_where_in` (`offload_plan.py`).
- Produces:
  - `forever.FOREVER_VIEWS = {"offloaded": MailState.OFFLOADED, "deleted": MailState.DELETED}`
  - `forever.ForeverProblem(ValueError)`, `forever.ForeverResult(deleted: int, left: int)`
  - `delete_forever(db, layout, bus, account_id, email, *, view: str, label: str | None = None, q: str = "", message_ids: list[int] | None = None, confirm: int, now: datetime, restore_queued: Callable[[int], bool] | None = None) -> ForeverResult`
  - `POST /api/mail/delete-forever` with body `{account_id, view, label?, q?, message_ids?, confirm}` gives `{"deleted", "left"}`, or 400 with a plain message.
  - `POST /api/mail/archive/delete-forever` no longer exists.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mail_forever.py
"""Delete forever in Offloaded and Deleted in Gmail (mail panel spec §7)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.devtools.fake_gmail import make_mail
from app.models import Event, MailJob, MailMessage, RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, JobState, MailJobKind, MailState
from app.services.events.bus import EventBus
from app.services.mail.forever import ForeverProblem, delete_forever
from app.services.mail.store import eml_path
from tests.rules_helpers import keep

NOW = datetime(2026, 10, 13, tzinfo=UTC)
NAMES = {"offloaded": "Offloaded", "deleted": "Deleted in Gmail"}
BOTH = [("offloaded", MailState.OFFLOADED), ("deleted", MailState.DELETED)]


def kept(db, layout, acc, subject, state):
    row = keep(db, layout, acc, make_mail(subject))
    row.state = state
    db.commit()
    return row


def forever(db, layout, acc, **kw):
    kw.setdefault("now", NOW)
    return delete_forever(db, layout, EventBus(), acc.id, acc.email, **kw)


@pytest.mark.parametrize("view,state", BOTH)
def test_picked_mails_lose_file_row_search_entry_and_rule_history(
    db, layout, make_account, view, state
):
    acc = make_account()
    a = kept(db, layout, acc, "Old promo", state)
    in_gmail = keep(db, layout, acc, make_mail("Still in Gmail"))
    db.add(
        RuleDecision(
            account_id=acc.id, message_id=a.id, how=DecisionHow.AI, status=DecisionStatus.APPLIED
        )
    )
    db.commit()
    path, a_id = eml_path(layout, acc.email, a.file_path), a.id
    res = forever(db, layout, acc, view=view, message_ids=[a.id, in_gmail.id], confirm=1)
    assert (res.deleted, res.left) == (1, 1)
    assert not path.exists() and db.get(MailMessage, a_id) is None
    rows = db.execute(text("SELECT count(*) FROM mail_fts WHERE rowid = :i"), {"i": a_id})
    assert rows.scalar() == 0
    assert db.scalar(select(RuleDecision).where(RuleDecision.message_id == a_id)) is None
    assert db.get(MailMessage, in_gmail.id) is not None  # mail in Gmail is never touched
    assert eml_path(layout, acc.email, in_gmail.file_path).exists()
    ev = db.scalar(select(Event).where(Event.kind == "mail_deleted_forever"))
    assert ev.message == f"Deleted 1 mail forever from {NAMES[view]}"
    assert "Old promo" in str(ev.data)


@pytest.mark.parametrize("view,state", BOTH)
def test_a_pick_all_is_the_view_and_the_search(db, layout, make_account, view, state):
    acc = make_account()
    kept(db, layout, acc, "promo one", state)
    kept(db, layout, acc, "promo two", state)
    kept(db, layout, acc, "receipt", state)
    other = MailState.DELETED if state == MailState.OFFLOADED else MailState.OFFLOADED
    kept(db, layout, acc, "promo elsewhere", other)
    keep(db, layout, acc, make_mail("promo in Gmail"))
    assert forever(db, layout, acc, view=view, q="promo", confirm=2).deleted == 2
    assert forever(db, layout, acc, view=view, q="", confirm=1).deleted == 1  # the whole view
    left = sorted(db.scalars(select(MailMessage.subject)))
    assert left == ["promo elsewhere", "promo in Gmail"]


def test_a_count_typed_for_an_older_pick_all_is_refused(db, layout, make_account):
    acc = make_account()
    kept(db, layout, acc, "a", MailState.DELETED)
    kept(db, layout, acc, "b", MailState.DELETED)
    kept(db, layout, acc, "c", MailState.DELETED)  # deleted in Gmail after the count was typed
    with pytest.raises(ForeverProblem, match=r"doesn't match the 3 mails picked"):
        forever(db, layout, acc, view="deleted", q="", confirm=2)
    assert db.scalar(select(MailMessage).where(MailMessage.subject == "a")) is not None


@pytest.mark.parametrize("view", ["all", "inbox", "archived", "suggested", "kept"])
def test_only_the_cleansync_views_take_it(db, layout, make_account, view):
    acc = make_account()
    keep(db, layout, acc, make_mail("In Gmail"))
    with pytest.raises(ForeverProblem, match="works in Offloaded and Deleted in Gmail"):
        forever(db, layout, acc, view=view, q="", confirm=1)


def test_an_empty_pick_never_falls_back_to_the_search(db, layout, make_account):
    acc = make_account()
    kept(db, layout, acc, "promo", MailState.OFFLOADED)
    with pytest.raises(ForeverProblem, match=r"^Pick mails or type a search\.$"):
        forever(db, layout, acc, view="offloaded", message_ids=[], q="promo", confirm=1)


def test_nothing_in_the_view_is_refused(db, layout, make_account):
    acc = make_account()
    row = keep(db, layout, acc, make_mail("In Gmail"))
    with pytest.raises(ForeverProblem, match=r"^None of these mails are in Offloaded\.$"):
        forever(db, layout, acc, view="offloaded", message_ids=[row.id], confirm=0)


def test_the_record_keeps_500_mails_and_says_how_many_more(db, layout, make_account):
    acc = make_account()
    for i in range(502):
        kept(db, layout, acc, f"mail {i}", MailState.OFFLOADED)
    assert forever(db, layout, acc, view="offloaded", q="", confirm=502).deleted == 502
    ev = db.scalar(select(Event).where(Event.kind == "mail_deleted_forever"))
    assert len(ev.data["mails"]) == 500 and ev.data["more"] == 2


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING, JobState.PAUSED])
def test_it_waits_for_a_restore(db, layout, make_account, state):
    acc = make_account()
    row = kept(db, layout, acc, "x", MailState.OFFLOADED)
    db.add(MailJob(account_id=acc.id, kind=MailJobKind.RESTORE, trigger="MANUAL", state=state))
    db.commit()
    with pytest.raises(ForeverProblem, match="A restore is running"):
        forever(db, layout, acc, view="offloaded", message_ids=[row.id], confirm=1)


def test_the_endpoint(auth_client, app, db, layout, make_account):
    acc = make_account()
    row = kept(db, layout, acc, "Promo", MailState.DELETED)
    url = "/api/mail/delete-forever"
    r = auth_client.post(url, json={"account_id": acc.id, "view": "deleted", "q": "", "confirm": 2})
    assert r.status_code == 400 and "doesn't match" in r.json()["detail"]
    app.state.mail.queue.waiting = lambda aid: [("restore", {})]
    r = auth_client.post(url, json={"account_id": acc.id, "view": "deleted", "q": "", "confirm": 1})
    assert r.status_code == 400 and "A restore is running" in r.json()["detail"]
    app.state.mail.queue.waiting = lambda aid: []
    body = {"account_id": acc.id, "view": "deleted", "message_ids": [row.id], "confirm": 1}
    assert auth_client.post(url, json=body).json() == {"deleted": 1, "left": 0}
    old = auth_client.post(
        "/api/mail/archive/delete-forever",
        json={"account_id": acc.id, "message_ids": [row.id], "confirm": 1},
    )
    assert old.status_code in (404, 405)
```

If `app.state.mail.queue` has no `waiting` attribute to replace in this fixture, set it the way the old `test_delete_forever_refuses_while_a_restore_is_queued` did (it assigned `app.state.mail.queue.waiting` directly).

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_mail_forever.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.mail.forever'`.

- [ ] **Step 3: Write `forever.py`**

```python
# backend/app/services/mail/forever.py
"""Delete forever (mail panel spec §7): removes CleanSync's own copy of mail already out of
Gmail (Offloaded, or Deleted in Gmail): the .eml, the search entry, the rule history and the
row. A short record goes to Activity. Mail still in Gmail needs round 3."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MailJob, MailMessage
from app.models.enums import JobState, MailJobKind, MailState
from app.paths import Layout
from app.services.events.bus import EventBus, record_event
from app.services.mail.index import delete_search_rows
from app.services.mail.offload_plan import rows_where_in
from app.services.mail.search import VIEW_ALIASES, SearchError, mail_conditions
from app.services.mail.store import eml_path

MAX_RECORDED = 500
FOREVER_VIEWS = {"offloaded": MailState.OFFLOADED, "deleted": MailState.DELETED}
VIEW_NAMES = {"offloaded": "Offloaded", "deleted": "Deleted in Gmail"}
RESTORE_RUNNING_MSG = "A restore is running for this mailbox. Try again when it's done."
NOTHING_PICKED_MSG = "Pick mails or type a search."
WRONG_VIEW_MSG = "Delete forever works in Offloaded and Deleted in Gmail."
NONE_HERE_MSG = "None of these mails are in {name}."
log = logging.getLogger(__name__)


class ForeverProblem(ValueError):
    pass


@dataclass
class ForeverResult:
    deleted: int
    left: int


def _mails(n: int) -> str:
    return f"{n:,} {'mail' if n == 1 else 'mails'}"


def forever_targets(
    db: Session,
    account_id: int,
    *,
    view: str,
    label: str | None,
    q: str,
    message_ids: list[int] | None,
    now: datetime,
) -> tuple[list[MailMessage], int]:
    """The mails to remove, and how many picked ones were left because they aren't in the
    view. A pick by hand, even an empty one, never falls back to the search."""
    state = FOREVER_VIEWS.get(view)
    if state is None:
        raise ForeverProblem(WRONG_VIEW_MSG)
    if message_ids is not None:
        if not message_ids:
            raise ForeverProblem(NOTHING_PICKED_MSG)
        rows = rows_where_in(db, account_id, MailMessage.id, message_ids)
        mine = [r for r in rows if r.state == state]
        return mine, len(rows) - len(mine)
    try:
        conds = mail_conditions(db, account_id, view=view, label=label, q=q, now=now)
    except SearchError as e:
        raise ForeverProblem(str(e)) from None
    return list(db.scalars(select(MailMessage).where(*conds))), 0


def delete_forever(
    db: Session,
    layout: Layout,
    bus: EventBus,
    account_id: int,
    email: str,
    *,
    view: str,
    label: str | None = None,
    q: str = "",
    message_ids: list[int] | None = None,
    confirm: int,
    now: datetime,
    restore_queued: Callable[[int], bool] | None = None,
) -> ForeverResult:
    """restore_queued(account_id): whether a restore waits in the queue (it has no job row
    until it starts)."""
    running = db.scalar(
        select(MailJob.id)
        .where(
            MailJob.account_id == account_id,
            MailJob.kind == MailJobKind.RESTORE,
            MailJob.state.in_([JobState.QUEUED, JobState.RUNNING, JobState.PAUSED]),
        )
        .limit(1)
    )
    if running is not None or (restore_queued is not None and restore_queued(account_id)):
        raise ForeverProblem(RESTORE_RUNNING_MSG)
    view = VIEW_ALIASES.get(view, view)
    rows, left = forever_targets(
        db, account_id, view=view, label=label, q=q, message_ids=message_ids, now=now
    )
    name = VIEW_NAMES[view]
    if not rows:
        raise ForeverProblem(NONE_HERE_MSG.format(name=name))
    if confirm != len(rows):
        raise ForeverProblem(f"The number you typed doesn't match the {_mails(len(rows))} picked.")
    record = [
        {"from": r.from_addr, "subject": r.subject, "date": r.date.isoformat()}
        for r in rows[:MAX_RECORDED]
    ]
    paths = [eml_path(layout, email, r.file_path) for r in rows]
    delete_search_rows(db, [r.id for r in rows], commit=False)  # one commit: rows, search, record
    for r in rows:
        db.delete(r)  # its decisions, to-do and offload items go with it (ON DELETE CASCADE)
    record_event(
        db,
        bus,
        kind="mail_deleted_forever",
        message=f"Deleted {_mails(len(rows))} forever from {name}",
        account_id=account_id,
        data={"mails": record, "more": max(0, len(rows) - MAX_RECORDED)},
    )  # commits
    for p in paths:  # after the commit: a leftover file is harmless, a row without one isn't
        try:
            p.unlink(missing_ok=True)
        except OSError as e:
            log.warning("could not remove %s: %s", p.name, e)
    return ForeverResult(len(rows), left)
```

- [ ] **Step 4: The endpoint**

```python
# backend/app/api/mail_forever.py
"""Delete forever in Offloaded and Deleted in Gmail (mail panel spec §7)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.mail_reader import account_or_404
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.services.mail.forever import ForeverProblem, delete_forever

router = APIRouter(prefix="/api/mail")


class ForeverIn(BaseModel):
    """Picked mails (message_ids), or a pick-all: the view, label and search."""

    account_id: int
    view: str = Field(max_length=32)
    label: str | None = Field(None, max_length=128)
    q: str = Field("", max_length=1000)
    message_ids: list[int] | None = Field(None, max_length=50000)
    confirm: int


@router.post("/delete-forever")
def forever(
    body: ForeverIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    acc = account_or_404(db, body.account_id)
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
    return {"deleted": res.deleted, "left": res.left}
```

In `backend/app/main.py`, replace the `mail_archive` import and its `include_router` entry with `mail_forever`. In `backend/app/api/rules.py`, replace `from app.services.mail.archive import ArchiveProblem` with `from app.services.mail.forever import ForeverProblem`, and `ArchiveProblem` in `PROBLEMS` with `ForeverProblem`.

Then: `git rm backend/app/services/mail/archive.py backend/app/api/mail_archive.py`

- [ ] **Step 5: Keep the old tests worth keeping, then remove their files**

Move these from `backend/tests/test_mail_archive.py` into `backend/tests/test_mail_offloaded_state.py`, with their imports (`select`, `MailMessage`, `_copies`, `_lose_first_import_answer`, `_offloaded`):
- `_archive`, renamed `_offloaded_by_a_rule`.
- `test_archived_mail_restores_untrashed_or_imported`, renamed `test_mail_a_rule_offloaded_restores_untrashed_or_imported`.
- `test_restore_the_whole_archive_by_search`, renamed `test_mail_a_rule_offloaded_restores_by_search`.
- `test_a_lost_import_answer_for_an_archived_mail_never_makes_a_second_copy`, renamed `test_a_lost_import_answer_for_mail_a_rule_offloaded_never_makes_a_second_copy`.

These tests now cover mail that a rule offloaded (`offload_id` empty) going back through the one restore path. Move `test_thread_list_carries_the_rule_chip` and its `mailbox` fixture from `backend/tests/test_mail_archive_api.py` too. Then:

`git rm backend/tests/test_mail_archive.py backend/tests/test_mail_archive_api.py`

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest tests/test_mail_forever.py tests/test_mail_offloaded_state.py tests/test_rules_api.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full backend suite and lint**

Run: `cd backend && grep -rn "mail_archive\|ArchiveProblem\|services.mail.archive" app tests; uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: the grep finds nothing. Tests and lint PASS.

- [ ] **Step 8: Commit**

```bash
git add -A backend/app backend/tests
git commit -m "feat(mail): Delete forever works in Offloaded and Deleted in Gmail, by pick or pick-all

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 6: The panel's two groups, the Batches tab, and search in the view

**Files:**
- Modify: `frontend/src/lib/mail.ts` (view lists, `viewKind`, `mailView`, `mailHref` with `tab`)
- Modify: `frontend/src/lib/mail.test.ts`
- Modify: `frontend/src/components/SectionPanel.tsx` (`MailViews`)
- Modify: `frontend/src/components/ContentBar.tsx` (the search stays in the view)
- Modify: `frontend/src/api/queries.ts` (`mailThreadQuery`, `useMailThread` take the view)
- Modify: `frontend/src/components/mail/MailThread.tsx`
- Modify: `frontend/src/components/mail/MailReader.tsx` (old `archive` view, the Offloaded tabs)
- Modify: `frontend/src/pages/MailOffloadPage.tsx` (`OffloadBatches` replaces `MailOffloadsPage`)
- Modify: `frontend/src/routes.tsx` (`/mail/offloads` redirects)
- Modify: `frontend/src/components/Shell.test.tsx`, `frontend/src/components/mail/MailReader.test.tsx`, `frontend/src/pages/MailOffloadPage.test.tsx`

**Interfaces:**
- Consumes: the backend views `archived`, `offloaded`, `deleted`, and `GET /api/mail/threads/{id}?view=` (Task 1).
- Produces (in `lib/mail.ts`):
  - `type MailViewDef = { id: string; label: string; icon: IconName }`
  - `GMAIL_VIEWS: MailViewDef[]` (Inbox, Starred, Important, Sent, Archived, All mail)
  - `CLEANSYNC_VIEWS: MailViewDef[]` (Suggested, Offloaded, Deleted in Gmail). Rules sits between Suggested and Offloaded in the panel.
  - `MAIL_VIEWS = [...GMAIL_VIEWS, ...CLEANSYNC_VIEWS]`
  - `type ViewKind = "gmail" | "out" | "suggested"`, and `viewKind(view: string): ViewKind`
  - `mailView(param: string | null): string` (null gives `"all"`; `"archive"` gives `"offloaded"`)
  - `mailHref(p: { view?; label?; q?; thread?; tab? })`
- Produces: `mailThreadQuery(accountId, threadId, view = "all")` and `useMailThread(accountId, threadId, view = "all")`, plus `OffloadBatches({ accountId }: { accountId: number })` in `pages/MailOffloadPage.tsx`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/lib/mail.test.ts`, change the import to `import { CLEANSYNC_VIEWS, GMAIL_VIEWS, labelDot, LABEL_DOTS, mailHref, mailView, viewKind, viewTitle } from "./mail";` and add:

```ts
  it("lists Gmail's views first and CleanSync's own after", () => {
    expect(GMAIL_VIEWS.map((v) => v.label)).toEqual(["Inbox", "Starred", "Important", "Sent", "Archived", "All mail"]);
    expect(CLEANSYNC_VIEWS.map((v) => v.label)).toEqual(["Suggested", "Offloaded", "Deleted in Gmail"]);
    expect(viewTitle("archived")).toBe("Archived");
  });

  it("knows which views hold mail in Gmail", () => {
    expect(["all", "inbox", "archived", "sent"].map(viewKind)).toEqual(["gmail", "gmail", "gmail", "gmail"]);
    expect(["offloaded", "deleted", "suggested"].map(viewKind)).toEqual(["out", "out", "suggested"]);
  });

  it("opens Offloaded for the old Local archive", () => {
    expect(mailView(null)).toBe("all");
    expect(mailView("archive")).toBe("offloaded");
    expect(mailView("inbox")).toBe("inbox");
    expect(mailHref({ view: "offloaded", tab: "batches" })).toBe("/mail?view=offloaded&tab=batches");
  });
```

In `frontend/src/components/Shell.test.tsx`, inside `describe("rail and panel", …)`, add:

```tsx
  it("puts the Gmail views first and CleanSync's own below its line", async () => {
    const active = { ...ACCOUNT, services: { files: "active" as const, mail: "active" as const } };
    mockApi({
      "GET /api/accounts": [active],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/status": STATUS,
      "GET /api/mail/counts": {
        views: { inbox: 3, starred: 1, important: 0, sent: 2, archived: 4, all: 9, suggested: 1, offloaded: 5, deleted: 6 },
        labels: [{ id: "Label_2", name: "Receipts", count: 2 }],
      },
    });
    renderApp(<AppShell />, { withAccount: true, route: "/mail?view=offloaded&tab=batches" });
    const views = await screen.findByRole("navigation", { name: "Mail views" });
    const names = (group: HTMLElement) => within(group).getAllByRole("link").map((l) => l.textContent?.replace(/\s+/g, " ").trim());
    const gmail = within(views).getByRole("group", { name: "Gmail" });
    await within(gmail).findByRole("link", { name: "Inbox 3" });
    expect(names(gmail)).toEqual(["Inbox 3", "Starred 1", "Important 0", "Sent 2", "Archived 4", "All mail 9", "Receipts 2"]);
    const own = within(views).getByRole("group", { name: "CleanSync" });
    expect(names(own)).toEqual(["Suggested 1", "Rules", "Offloaded 5", "Deleted in Gmail 6"]);
    expect(within(own).getByRole("link", { name: "Offloaded 5" })).toHaveAttribute("aria-current", "page");
    expect(within(views).getByText("CleanSync")).toBeInTheDocument();
    expect(within(views).queryByRole("link", { name: /Local archive/ })).not.toBeInTheDocument();
  });
```

In the same file, inside `describe("content bar", …)`, add:

```tsx
  it("searches mail in the view and label on screen, and an empty search clears it", async () => {
    await open("/mail?view=inbox&label=Label_2");
    const box = screen.getByRole("textbox", { name: "Search mail" });
    await userEvent.type(box, "from:bank{Enter}");
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/mail?view=inbox&label=Label_2&q=from%3Abank");
    await userEvent.clear(box);
    await userEvent.type(box, "{Enter}");
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/mail\?view=inbox&label=Label_2$/);
  });
```

In `frontend/src/components/mail/MailReader.test.tsx`, add a new `describe`:

```tsx
describe("the CleanSync views", () => {
  const OFF = {
    id: 7, account_id: 1, filter_text: "older_than:1y", state: "DONE", total: 3, done: 3, skipped: 0, not_done: 0,
    bytes: 3000, reason: null, created_at: "2026-10-10T09:00:00Z", finished_at: "2026-10-10T09:20:00Z",
    trash_until: "2026-11-09", google_emptied: false,
  };

  it("opens Offloaded for an old Local archive link", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [], next_cursor: null, total: 0, total_bytes: 0 },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?view=archive" });
    expect(await screen.findByRole("heading", { name: "Offloaded" })).toBeInTheDocument();
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/threads")?.url.searchParams.get("view")).toBe("offloaded"));
  });

  it("shows the offload batches in a tab of Offloaded", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [], next_cursor: null, total: 0, total_bytes: 0 },
      "GET /api/mail/offloads": [OFF],
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?view=offloaded&tab=batches" });
    const tabs = await screen.findByRole("navigation", { name: "Offloaded" });
    expect(within(tabs).getByRole("link", { name: "Batches" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Mail" })).toHaveAttribute("href", "/mail?view=offloaded");
    expect(await screen.findByRole("link", { name: "older_than:1y" })).toHaveAttribute("href", "/mail/offloads/7");
    expect(screen.queryByRole("list", { name: "Mail" })).not.toBeInTheDocument();
  });

  it("opens a thread with the mails of the view it is in", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1", offloaded: true })], next_cursor: null, total: 1, total_bytes: 1 },
      "GET /api/mail/threads/t1": [msg({ id: 2, state: "offloaded", gmail_url: null })],
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?view=offloaded&thread=t1" });
    await screen.findByRole("region", { name: "Open mail" });
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/threads/t1")?.url.searchParams.get("view")).toBe("offloaded"));
  });
});
```

In `frontend/src/pages/MailOffloadPage.test.tsx`, import `OffloadBatches` in place of `MailOffloadsPage`, and replace the test "lists every offload" with:

```tsx
  it("lists every offload batch", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/mail/offloads": [OFF] });
    renderApp(<OffloadBatches accountId={ACCOUNT.id} />, { withAccount: true, route: "/mail?view=offloaded&tab=batches" });
    expect(await screen.findByRole("link", { name: "older_than:1y" })).toHaveAttribute("href", "/mail/offloads/7");
  });
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd frontend && npm test -- --run src/lib/mail.test.ts src/components/Shell.test.tsx src/components/mail/MailReader.test.tsx src/pages/MailOffloadPage.test.tsx`
Expected: FAIL. `GMAIL_VIEWS` isn't exported, there are no groups or tabs, and the search drops the view.

- [ ] **Step 3: `lib/mail.ts`**

Replace `MAIL_VIEWS` and `viewTitle` with:

```ts
export type MailViewDef = { id: string; label: string; icon: IconName };

/** Mail panel spec §4: Gmail's own folders as they are, then what CleanSync adds. */
export const GMAIL_VIEWS: MailViewDef[] = [
  { id: "inbox", label: "Inbox", icon: "inbox" },
  { id: "starred", label: "Starred", icon: "star" },
  { id: "important", label: "Important", icon: "label_important" },
  { id: "sent", label: "Sent", icon: "send" },
  { id: "archived", label: "Archived", icon: "archive" },
  { id: "all", label: "All mail", icon: "mail" },
];
/** Rules sits between Suggested and Offloaded in the panel; it's a page, not a view. */
export const CLEANSYNC_VIEWS: MailViewDef[] = [
  { id: "suggested", label: "Suggested", icon: "auto_awesome" },
  { id: "offloaded", label: "Offloaded", icon: "inventory_2" },
  { id: "deleted", label: "Deleted in Gmail", icon: "delete" },
];
export const MAIL_VIEWS = [...GMAIL_VIEWS, ...CLEANSYNC_VIEWS];
const OUT_OF_GMAIL = ["offloaded", "deleted"];

/** "gmail": mail in Gmail; "out": CleanSync's copy of mail no longer in Gmail; "suggested". */
export type ViewKind = "gmail" | "out" | "suggested";
export function viewKind(view: string): ViewKind {
  if (view === "suggested") return "suggested";
  return OUT_OF_GMAIL.includes(view) ? "out" : "gmail";
}

/** The view a Mail address asks for. Old links to the Local archive open Offloaded. */
export function mailView(param: string | null): string {
  const view = param || "all";
  return view === "archive" ? "offloaded" : view;
}

export function viewTitle(view: string, labelName?: string | null): string {
  if (labelName) return labelName;
  return MAIL_VIEWS.find((v) => v.id === view)?.label ?? "All mail";
}
```

In `mailHref`, add `tab?: string` to the parameter type and `if (p.tab) params.set("tab", p.tab);` after the `q` line. In the comment of `runRulesStart`, "the Local archive" becomes "Offloaded".

- [ ] **Step 4: The panel**

In `frontend/src/components/SectionPanel.tsx`, change the import from `../lib/mail` to `import { CLEANSYNC_VIEWS, GMAIL_VIEWS, labelDot, type MailViewDef, mailHref, mailView } from "../lib/mail";` and replace `MailViews` with:

```tsx
/** Mail panel spec §4: Gmail's views and labels, then a line, then CleanSync's own views. */
export function MailViews() {
  const { account } = useCurrentAccount();
  const counts = useMailCounts(account?.id ?? null).data;
  const { pathname, search } = useLocation();
  // Only the mail list lights a view: not the Rules page or an offload's page under /mail.
  const onList = pathname === "/mail";
  // Offloaded stays lit on its Batches tab and on an offload's own page.
  const offloadedOn = (onList && mailView(new URLSearchParams(search).get("view")) === "offloaded") || pathname.startsWith("/mail/offloads");
  const [open, setOpenState] = useState(labelsOpen);
  const setOpen = (next: boolean) => {
    setOpenState(next);
    try {
      localStorage.setItem(LABELS_OPEN_KEY, next ? "1" : "0");
    } catch {
      // storage blocked: the fold lasts for this visit only
    }
  };
  const link = (v: MailViewDef) => (
    <PanelLink
      key={v.id}
      section="mail"
      to={mailHref({ view: v.id })}
      label={v.label}
      icon={v.icon}
      count={counts?.views[v.id]}
      active={v.id === "offloaded" ? offloadedOn : onList ? undefined : false}
    />
  );
  return (
    <>
      <div role="group" aria-label="Gmail" className="flex flex-col gap-0.5">
        <PanelHeading>Gmail</PanelHeading>
        {GMAIL_VIEWS.map(link)}
        {counts && counts.labels.length > 0 && (
          <>
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpen(!open)}
              className="mt-3 flex items-center gap-1 px-2.5 py-1 text-left text-meta font-bold uppercase tracking-[0.06em] text-ink-2 hover:text-ink"
            >
              Labels
              <Icon name="keyboard_arrow_down" className={`text-[16px] transition-transform ${open ? "" : "-rotate-90"}`} />
            </button>
            {open && (
              <ul aria-label="Labels" className="flex flex-col gap-0.5">
                {counts.labels.map((l) => (
                  <li key={l.id}>
                    <PanelLink
                      section="mail"
                      to={mailHref({ label: l.id })}
                      label={l.name}
                      count={l.count}
                      badge={<span aria-hidden="true" className={`order-first h-2 w-2 shrink-0 rounded-full ${labelDot(l.id)}`} />}
                    />
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
      <div role="group" aria-label="CleanSync" className="mt-4 flex flex-col gap-0.5">
        <div className="flex items-center gap-2 px-2.5 py-1 text-meta font-bold uppercase tracking-[0.06em] text-ink-2">
          <span aria-hidden="true" className="h-px flex-1 bg-line" />
          CleanSync
          <span aria-hidden="true" className="h-px flex-1 bg-line" />
        </div>
        {link(CLEANSYNC_VIEWS[0])}
        <PanelLink section="mail" to="/mail/rules" label="Rules" icon="view_list" />
        {CLEANSYNC_VIEWS.slice(1).map(link)}
      </div>
    </>
  );
}
```

- [ ] **Step 5: The search stays in the view**

In `frontend/src/components/ContentBar.tsx`, import `useLocation` from `react-router-dom` and `mailHref` from `../lib/mail`. Replace `submit` with:

```tsx
  const { pathname } = useLocation();
  /** Mail panel spec §5: the search runs in the view and label on screen. An empty search
   * clears it; one letter finds too much (the hint says 2). */
  const searchMail = (text: string) => {
    if (text.length === 1) return;
    const here = pathname === "/mail";
    navigate(mailHref({ view: here ? (params.get("view") ?? undefined) : undefined, label: here ? (params.get("label") ?? undefined) : undefined, q: text || undefined }));
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = q.trim();
    if (mail) return searchMail(text);
    if (text.length < 2) return;
    navigate(`/search?q=${encodeURIComponent(text)}`);
  };
```

- [ ] **Step 6: A thread opens with its view**

In `frontend/src/api/queries.ts`, replace `mailThreadQuery` and `useMailThread` with:

```ts
/** One thread's mails that belong to `view` (mail panel spec §4): in a Gmail view the ones in
 * Gmail, in Offloaded the offloaded ones. Also used with `queryClient.fetchQuery` to learn
 * the mails of picked threads. */
export const mailThreadQuery = (accountId: number, threadId: string, view = "all") => ({
  queryKey: ["mail-thread", accountId, threadId, view],
  queryFn: () =>
    api<MailMessageInfo[]>(`/api/mail/threads/${encodeURIComponent(threadId)}${query({ account_id: accountId, view: view === "all" ? undefined : view })}`),
});

export const useMailThread = (accountId: number, threadId: string, view = "all") =>
  useQuery({ ...mailThreadQuery(accountId, threadId, view), enabled: threadId !== "" });
```

In `frontend/src/components/mail/MailThread.tsx`, import `mailView` from `../../lib/mail`, and the query becomes `useMailThread(account.id, threadId, mailView(params.get("view")))`. In `MailReader.tsx`, `pickForever` calls `mailThreadQuery(account.id, id, view)` instead of passing `true` (Task 8 rewrites it anyway).

- [ ] **Step 7: Batches**

In `frontend/src/pages/MailOffloadPage.tsx`:
- The back link becomes `<Link to="/mail?view=offloaded&tab=batches" className="text-table text-accent">Offload batches</Link>`.
- Replace `MailOffloadsPage` with:

```tsx
/** The Batches tab of Offloaded (mail panel spec §4): offloads started from a filter or a
 * pick, with the space each freed. Mail offloaded one at a time or by a rule isn't listed. */
export function OffloadBatches({ accountId }: { accountId: number }) {
  const list = useMailOffloads(accountId).data ?? [];
  if (list.length === 0) return <Empty icon="inventory_2" title="No offload batches yet" />;
  return (
    <ul aria-label="Offload batches" className="divide-y divide-line rounded-card border border-line">
      {list.map((o) => (
        <li key={o.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-table">
          <Link to={`/mail/offloads/${o.id}`} className="font-semibold text-accent">
            {o.filter_text}
          </Link>
          <span className="text-ink-2">{OFFLOAD_STATE_TEXT[o.state] ?? o.state}</span>
          <span className="tabular text-ink-2">
            {o.done.toLocaleString()} mails · {formatBytes(o.bytes)}
          </span>
          <span className="ml-auto text-ink-2">{formatWhen(o.created_at)}</span>
        </li>
      ))}
    </ul>
  );
}
```

In `frontend/src/routes.tsx`, import only `MailOffloadPage` from `./pages/MailOffloadPage`, and the `mail/offloads` route becomes `<Route path="mail/offloads" element={<Navigate to="/mail?view=offloaded&tab=batches" replace />} />`.

- [ ] **Step 8: The reader's view and tabs**

In `frontend/src/components/mail/MailReader.tsx`:
- Import `Link` from `react-router-dom`, `mailHref` and `mailView` from `../../lib/mail`, and `OffloadBatches` from `../../pages/MailOffloadPage`.
- `const view = params.get("view") ?? "all";` becomes `const view = mailView(params.get("view"));`, and add `const batches = view === "offloaded" && params.get("tab") === "batches";`.
- Right after the header `div` (before the "Picked mail" section), add:

```tsx
      {view === "offloaded" && (
        <nav aria-label="Offloaded" className="flex gap-4 px-4 pb-2 text-table font-semibold sm:px-6">
          <Link to={mailHref({ view: "offloaded" })} aria-current={batches ? undefined : "page"} className={batches ? "text-ink-2" : "text-accent"}>
            Mail
          </Link>
          <Link to={mailHref({ view: "offloaded", tab: "batches" })} aria-current={batches ? "page" : undefined} className={batches ? "text-accent" : "text-ink-2"}>
            Batches
          </Link>
        </nav>
      )}
```

- Wrap the list-and-thread `div` (the one with `className="flex min-h-0 flex-1 border-t border-line"`) as:

```tsx
      {batches ? (
        <div className="min-h-0 flex-1 overflow-y-auto border-t border-line p-4 sm:p-6">
          <OffloadBatches accountId={account.id} />
        </div>
      ) : (
        /* the existing list-and-thread div, unchanged */
      )}
```

- [ ] **Step 9: Run the frontend checks**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: PASS. If an existing test named the old view list order (for example `MAIL_VIEWS[0]` being All mail), update it to the new order.

- [ ] **Step 10: Commit**

```bash
git add frontend/src
git commit -m "feat(mail): the panel shows Gmail's views, then CleanSync's; Offloaded gets a Batches tab; search stays in the view

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The search filter form

**Files:**
- Create: `frontend/src/lib/mailSearch.ts`
- Create: `frontend/src/lib/mailSearch.test.ts`
- Create: `frontend/src/components/mail/SearchOptions.tsx`
- Modify: `frontend/src/components/ContentBar.tsx` (the "Search options" button and form)
- Modify: `frontend/src/components/Icon.tsx` (the `tune` icon)
- Modify: `frontend/src/components/Shell.test.tsx`

**Interfaces:**
- Consumes: Task 6's `searchMail` in `ContentBar` and `mailView`.
- Produces (in `lib/mailSearch.ts`):
  - `type SearchFields = { from; to; words; label: string; sizeOp: "larger" | "smaller"; size: string; sizeUnit: "KB" | "MB"; date: "any" | "older" | "newer" | "before" | "after"; age: string; ageUnit: "d" | "m" | "y"; day: string; attachment; unread; starred: boolean }`
  - `EMPTY_SEARCH: SearchFields`
  - `writeSearch(f: SearchFields): string`
  - `readSearch(text: string, labelNames?: string[]): SearchFields`
  - `isNumber(v: string): boolean` (decimals allowed), `isWhole(v: string): boolean`
- Produces: `SearchOptions({ text, view, labels, onSearch, onClose })`.

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/src/lib/mailSearch.test.ts
import { describe, expect, it } from "vitest";

import { EMPTY_SEARCH, isNumber, isWhole, readSearch, writeSearch } from "./mailSearch";

describe("the search form's words", () => {
  it("writes each field's keyword, in the form's order", () => {
    expect(
      writeSearch({
        ...EMPTY_SEARCH,
        from: "Example News",
        to: "me@example.com",
        words: "invoice",
        label: "Receipts",
        sizeOp: "smaller",
        size: "5",
        sizeUnit: "KB",
        date: "older",
        age: "2",
        ageUnit: "y",
        attachment: true,
        unread: true,
        starred: true,
      }),
    ).toBe('from:"Example News" to:me@example.com invoice label:Receipts smaller:5K older_than:2y has:attachment is:unread is:starred');
    expect(writeSearch({ ...EMPTY_SEARCH, size: "1.5", date: "newer", age: "30", ageUnit: "d" })).toBe("larger:1.5M newer_than:30d");
    expect(writeSearch({ ...EMPTY_SEARCH, date: "before", day: "2024-01-31" })).toBe("before:2024-01-31");
    expect(writeSearch({ ...EMPTY_SEARCH, date: "after", day: "2024-01-01" })).toBe("after:2024-01-01");
    expect(writeSearch({ ...EMPTY_SEARCH, date: "older", age: "" })).toBe("");
    expect(writeSearch(EMPTY_SEARCH)).toBe("");
  });

  it("reads the keywords it knows back into the fields", () => {
    const f = readSearch('from:"Example News" larger:2MB before:2024-01-31 has:attachment is:starred shoes label:receipts', ["Receipts"]);
    expect(f).toEqual({ ...EMPTY_SEARCH, from: "Example News", size: "2", sizeUnit: "MB", date: "before", day: "2024-01-31", attachment: true, starred: true, words: "shoes", label: "Receipts" });
    expect(readSearch("smaller:500k older_than:6m is:unread")).toEqual({ ...EMPTY_SEARCH, sizeOp: "smaller", size: "500", sizeUnit: "KB", date: "older", age: "6", ageUnit: "m", unread: true });
  });

  it("keeps what it can't read in Has the words", () => {
    const f = readSearch('from:a@x.com from:b@x.com "big deal" larger:2G in:archived label:Nope newer_than:1y before:2024-01-01');
    expect(f.from).toBe("a@x.com");
    expect(f.date).toBe("newer");
    expect(f.words).toBe('from:b@x.com "big deal" larger:2G in:archived label:Nope before:2024-01-01');
    expect(writeSearch(f)).toBe('from:a@x.com from:b@x.com "big deal" larger:2G in:archived label:Nope before:2024-01-01 newer_than:1y');
  });

  it("checks numbers", () => {
    expect([isNumber("5"), isNumber("1.5"), isNumber(" 2 "), isNumber("abc"), isNumber("1e3")]).toEqual([true, true, true, false, false]);
    expect([isWhole("30"), isWhole("1.5"), isWhole("")]).toEqual([true, false, false]);
  });
});
```

In `frontend/src/components/Shell.test.tsx`, add (with `SearchOptions` reached through `ContentBar`):

```tsx
describe("search options", () => {
  const openMail = async (route: string) => {
    mockApi({
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/mail/counts": { views: {}, labels: [{ id: "Label_2", name: "Receipts", count: 4 }] },
    });
    renderApp(<ContentBar section="mail" onViews={() => undefined} />, { withAccount: true, route });
    await userEvent.click(screen.getByRole("button", { name: "Search options" }));
    return screen.getByRole("region", { name: "Search options" });
  };
  const box = () => screen.getByRole("textbox", { name: "Search mail" });

  it("builds the search from the form and runs it in the view", async () => {
    const form = await openMail("/mail?view=inbox");
    await userEvent.type(within(form).getByLabelText("From"), "Example News");
    await userEvent.type(within(form).getByLabelText("To"), "me@example.com");
    await userEvent.type(within(form).getByLabelText("Has the words"), "invoice");
    await within(form).findByRole("option", { name: "Receipts" });
    await userEvent.selectOptions(within(form).getByLabelText("Label"), "Receipts");
    await userEvent.selectOptions(within(form).getByLabelText("Larger or smaller"), "smaller");
    await userEvent.type(within(form).getByLabelText("Size"), "5");
    await userEvent.selectOptions(within(form).getByLabelText("Size unit"), "KB");
    await userEvent.selectOptions(within(form).getByLabelText("Date"), "older");
    await userEvent.type(within(form).getByLabelText("Age"), "2");
    await userEvent.selectOptions(within(form).getByLabelText("Age unit"), "y");
    await userEvent.click(within(form).getByLabelText("Has attachment"));
    await userEvent.click(within(form).getByLabelText("Unread"));
    await userEvent.click(within(form).getByLabelText("Starred"));
    await userEvent.click(within(form).getByRole("button", { name: "Search" }));
    const text = 'from:"Example News" to:me@example.com invoice label:Receipts smaller:5K older_than:2y has:attachment is:unread is:starred';
    expect(box()).toHaveValue(text);
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent(`/mail?${new URLSearchParams({ view: "inbox", q: text })}`);
    expect(screen.queryByRole("region", { name: "Search options" })).not.toBeInTheDocument();
  });

  it("reads the search box back into the form; Clear empties the form; closing changes nothing", async () => {
    const q = 'from:"Example News" larger:2M before:2024-01-31 has:attachment shoes';
    const form = await openMail(`/mail?${new URLSearchParams({ q })}`);
    expect(within(form).getByLabelText("From")).toHaveValue("Example News");
    expect(within(form).getByLabelText("Size")).toHaveValue("2");
    expect(within(form).getByLabelText("Size unit")).toHaveValue("MB");
    expect(within(form).getByLabelText("Date")).toHaveValue("before");
    expect(within(form).getByLabelText("Day")).toHaveValue("2024-01-31");
    expect(within(form).getByLabelText("Has attachment")).toBeChecked();
    expect(within(form).getByLabelText("Has the words")).toHaveValue("shoes");
    await userEvent.click(within(form).getByRole("button", { name: "Clear" }));
    expect(within(form).getByLabelText("From")).toHaveValue("");
    expect(within(form).getByLabelText("Has attachment")).not.toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Search options" }));
    expect(screen.queryByRole("region", { name: "Search options" })).not.toBeInTheDocument();
    expect(box()).toHaveValue(q);
  });

  it("keeps what it can't read in Has the words, so nothing is lost", async () => {
    const q = 'from:a@x.com from:b@x.com "big deal" larger:2G in:archived label:Nope';
    const form = await openMail(`/mail?${new URLSearchParams({ q })}`);
    await within(form).findByRole("option", { name: "Receipts" });
    expect(within(form).getByLabelText("Has the words")).toHaveValue('from:b@x.com "big deal" larger:2G in:archived label:Nope');
    await userEvent.click(within(form).getByRole("button", { name: "Search" }));
    expect(box()).toHaveValue(q);
  });

  it("has no Label in Deleted in Gmail, where a typed label stays in the words", async () => {
    const form = await openMail(`/mail?${new URLSearchParams({ view: "deleted", q: "label:Receipts" })}`);
    expect(within(form).queryByLabelText("Label")).not.toBeInTheDocument();
    expect(within(form).getByLabelText("Has the words")).toHaveValue("label:Receipts");
  });

  it("takes only numbers for the size and the age, and closes on Escape", async () => {
    const form = await openMail("/mail");
    await userEvent.type(within(form).getByLabelText("Size"), "abc");
    expect(within(form).getByText("Use a number.")).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Search" })).toBeDisabled();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("region", { name: "Search options" })).not.toBeInTheDocument();
  });

  it("is offered on the mail list only", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR] });
    renderApp(<ContentBar section="mail" onViews={() => undefined} />, { withAccount: true, route: "/mail/rules" });
    expect(screen.queryByRole("button", { name: "Search options" })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd frontend && npm test -- --run src/lib/mailSearch.test.ts src/components/Shell.test.tsx`
Expected: FAIL. `./mailSearch` doesn't exist, and there is no "Search options" button.

- [ ] **Step 3: `lib/mailSearch.ts`**

```ts
// frontend/src/lib/mailSearch.ts
/**
 * Mail panel spec §5: the search form writes the search box's keywords, and reads them back.
 * The backend parser (search.py) stays the only real one: this knows just the keywords the
 * form has fields for. Anything else stays in "Has the words", as typed.
 */

export type SizeUnit = "KB" | "MB";
export type AgeUnit = "d" | "m" | "y";
export type DateKind = "any" | "older" | "newer" | "before" | "after";
export type SearchFields = {
  from: string;
  to: string;
  words: string;
  label: string;
  sizeOp: "larger" | "smaller";
  size: string;
  sizeUnit: SizeUnit;
  date: DateKind;
  age: string;
  ageUnit: AgeUnit;
  day: string;
  attachment: boolean;
  unread: boolean;
  starred: boolean;
};

export const EMPTY_SEARCH: SearchFields = {
  from: "",
  to: "",
  words: "",
  label: "",
  sizeOp: "larger",
  size: "",
  sizeUnit: "MB",
  date: "any",
  age: "",
  ageUnit: "y",
  day: "",
  attachment: false,
  unread: false,
  starred: false,
};

export const isNumber = (v: string) => /^\d+(\.\d+)?$/.test(v.trim());
export const isWhole = (v: string) => /^\d+$/.test(v.trim());
const quote = (v: string) => (/\s/.test(v) ? `"${v}"` : v);

/** The order of the form: from, to, the words, label, size, date, then the ticks. */
export function writeSearch(f: SearchFields): string {
  const parts: string[] = [];
  const add = (key: string, v: string) => {
    const t = v.trim();
    if (t) parts.push(`${key}:${quote(t)}`);
  };
  add("from", f.from);
  add("to", f.to);
  if (f.words.trim()) parts.push(f.words.trim());
  add("label", f.label);
  if (f.size.trim()) parts.push(`${f.sizeOp}:${f.size.trim()}${f.sizeUnit === "MB" ? "M" : "K"}`);
  if ((f.date === "older" || f.date === "newer") && f.age.trim()) parts.push(`${f.date === "older" ? "older_than" : "newer_than"}:${f.age.trim()}${f.ageUnit}`);
  if ((f.date === "before" || f.date === "after") && f.day) parts.push(`${f.date}:${f.day}`);
  if (f.attachment) parts.push("has:attachment");
  if (f.unread) parts.push("is:unread");
  if (f.starred) parts.push("is:starred");
  return parts.join(" ");
}

// The same tokens as the backend: an optional "key:", then a quoted value or a plain word.
const TOKEN = /(?:[A-Za-z_]+:)?"[^"]*"?|\S+/g;
const SIZE = /^(\d+(?:\.\d+)?)([km])b?$/i;
const AGE = /^(\d+)([dmy])$/i;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

function unquote(v: string): string {
  let s = v;
  if (s.startsWith('"')) {
    s = s.slice(1);
    if (s.endsWith('"')) s = s.slice(0, -1);
  }
  return s.trim();
}

/** `labelNames`: the mailbox's labels. A label: that isn't one of them stays in the words. */
export function readSearch(text: string, labelNames: string[] = []): SearchFields {
  const f: SearchFields = { ...EMPTY_SEARCH };
  const rest: string[] = [];
  let sized = false;
  let dated = false;
  for (const tok of text.match(TOKEN) ?? []) {
    const at = tok.indexOf(":");
    const key = at > 0 && !tok.startsWith('"') ? tok.slice(0, at).toLowerCase() : "";
    const value = key ? unquote(tok.slice(at + 1)) : "";
    let used = false;
    if (key === "from" && value && !f.from) {
      f.from = value;
      used = true;
    } else if (key === "to" && value && !f.to) {
      f.to = value;
      used = true;
    } else if (key === "label" && value && !f.label) {
      const name = labelNames.find((n) => n.toLowerCase() === value.toLowerCase());
      if (name) {
        f.label = name;
        used = true;
      }
    } else if ((key === "larger" || key === "smaller") && !sized) {
      const m = SIZE.exec(value);
      if (m) {
        f.sizeOp = key;
        f.size = m[1];
        f.sizeUnit = m[2].toLowerCase() === "m" ? "MB" : "KB";
        sized = used = true;
      }
    } else if ((key === "older_than" || key === "newer_than") && !dated) {
      const m = AGE.exec(value);
      if (m) {
        f.date = key === "older_than" ? "older" : "newer";
        f.age = m[1];
        f.ageUnit = m[2].toLowerCase() as AgeUnit;
        dated = used = true;
      }
    } else if ((key === "before" || key === "after") && !dated && DAY.test(value)) {
      f.date = key;
      f.day = value;
      dated = used = true;
    } else if (key === "has" && /^attachments?$/i.test(value)) {
      f.attachment = used = true;
    } else if (key === "is" && value.toLowerCase() === "unread") {
      f.unread = used = true;
    } else if (key === "is" && value.toLowerCase() === "starred") {
      f.starred = used = true;
    }
    if (!used) rest.push(tok);
  }
  f.words = rest.join(" ");
  return f;
}
```

- [ ] **Step 4: The form**

Add the icon: in `frontend/src/components/Icon.tsx`, add `import TuneSvg from "@material-symbols/svg-400/rounded/tune.svg?react";` in alphabetical order, and `tune: TuneSvg,` to `ICONS`.

```tsx
// frontend/src/components/mail/SearchOptions.tsx
import { type KeyboardEvent, useState } from "react";

import { type AgeUnit, type DateKind, EMPTY_SEARCH, isNumber, isWhole, readSearch, type SearchFields, type SizeUnit, writeSearch } from "../../lib/mailSearch";
import { Button, Field, inputClass } from "../ui";

const DATES: [DateKind, string][] = [
  ["any", "Any time"],
  ["older", "Older than"],
  ["newer", "Newer than"],
  ["before", "Before a date"],
  ["after", "After a date"],
];

/** Mail panel spec §5: the form below the search box. It opens with the box read into its
 * fields; Search writes the box and runs it; Clear empties the form; closing changes nothing.
 * In Deleted in Gmail labels aren't reliable, so there is no Label field there. */
export function SearchOptions({
  text,
  view,
  labels,
  onSearch,
  onClose,
}: {
  text: string;
  view: string;
  labels: string[];
  onSearch: (q: string) => void;
  onClose: () => void;
}) {
  const showLabel = view !== "deleted";
  const names = showLabel ? labels : [];
  const [f, setF] = useState<SearchFields>(() => readSearch(text, names));
  // The labels may arrive after the form opened: read the box again once they do.
  const [readWith, setReadWith] = useState(names.join("\n"));
  if (readWith !== names.join("\n")) {
    setReadWith(names.join("\n"));
    setF(readSearch(writeSearch(f), names));
  }
  const set = (patch: Partial<SearchFields>) => setF({ ...f, ...patch });
  const aged = f.date === "older" || f.date === "newer";
  const badSize = f.size.trim() !== "" && !isNumber(f.size);
  const badAge = aged && f.age.trim() !== "" && !isWhole(f.age);
  const escape = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
    }
  };
  return (
    <section aria-label="Search options" onKeyDown={escape} className="absolute left-0 right-0 top-full z-30 mt-1 rounded-card border border-line bg-surface p-4 shadow-lg">
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!badSize && !badAge) onSearch(writeSearch(f));
        }}
      >
        <Field label="From">
          <input className={inputClass} value={f.from} onChange={(e) => set({ from: e.target.value })} />
        </Field>
        <Field label="To">
          <input className={inputClass} value={f.to} onChange={(e) => set({ to: e.target.value })} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Has the words">
            <input className={inputClass} value={f.words} onChange={(e) => set({ words: e.target.value })} />
          </Field>
        </div>
        {showLabel && (
          <Field label="Label">
            <select className={inputClass} value={f.label} onChange={(e) => set({ label: e.target.value })}>
              <option value="">Any</option>
              {labels.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </Field>
        )}
        <fieldset>
          <legend className="mb-1 text-table font-medium text-ink-2">Size</legend>
          <div className="flex gap-2">
            <select aria-label="Larger or smaller" className={`${inputClass} w-32`} value={f.sizeOp} onChange={(e) => set({ sizeOp: e.target.value as SearchFields["sizeOp"] })}>
              <option value="larger">Larger than</option>
              <option value="smaller">Smaller than</option>
            </select>
            <input aria-label="Size" inputMode="decimal" className={`${inputClass} w-20`} value={f.size} onChange={(e) => set({ size: e.target.value })} />
            <select aria-label="Size unit" className={`${inputClass} w-20`} value={f.sizeUnit} onChange={(e) => set({ sizeUnit: e.target.value as SizeUnit })}>
              <option value="KB">KB</option>
              <option value="MB">MB</option>
            </select>
          </div>
          {badSize && <p className="mt-1 text-meta text-err">Use a number.</p>}
        </fieldset>
        <fieldset>
          <legend className="mb-1 text-table font-medium text-ink-2">Date</legend>
          <div className="flex flex-wrap gap-2">
            <select aria-label="Date" className={`${inputClass} w-40`} value={f.date} onChange={(e) => set({ date: e.target.value as DateKind })}>
              {DATES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
            {aged && (
              <>
                <input aria-label="Age" inputMode="numeric" className={`${inputClass} w-20`} value={f.age} onChange={(e) => set({ age: e.target.value })} />
                <select aria-label="Age unit" className={`${inputClass} w-28`} value={f.ageUnit} onChange={(e) => set({ ageUnit: e.target.value as AgeUnit })}>
                  <option value="d">days</option>
                  <option value="m">months</option>
                  <option value="y">years</option>
                </select>
              </>
            )}
            {(f.date === "before" || f.date === "after") && (
              <input aria-label="Day" type="date" className={`${inputClass} w-44`} value={f.day} onChange={(e) => set({ day: e.target.value })} />
            )}
          </div>
          {badAge && <p className="mt-1 text-meta text-err">Use a number.</p>}
        </fieldset>
        <div className="flex flex-wrap gap-x-5 gap-y-2 sm:col-span-2">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={f.attachment} onChange={(e) => set({ attachment: e.target.checked })} />
            Has attachment
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={f.unread} onChange={(e) => set({ unread: e.target.checked })} />
            Unread
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={f.starred} onChange={(e) => set({ starred: e.target.checked })} />
            Starred
          </label>
        </div>
        <div className="flex justify-end gap-2 sm:col-span-2">
          <Button onClick={() => setF(EMPTY_SEARCH)}>Clear</Button>
          <Button type="submit" tone="primary" disabled={badSize || badAge}>
            Search
          </Button>
        </div>
      </form>
    </section>
  );
}
```

`text-err` is the colour the menus already use for danger items (`ui.tsx`).

- [ ] **Step 5: Put the form in the content bar**

In `frontend/src/components/ContentBar.tsx`:
- Import `useMailCounts` from `../api/queries`, `useCurrentAccount` from `../hooks/useAccount`, `mailView` from `../lib/mail`, and `SearchOptions` from `./mail/SearchOptions`.
- Add state and labels next to the other hooks:

```tsx
  const { accountId } = useCurrentAccount();
  const [options, setOptions] = useState(false);
  const onList = pathname === "/mail";
  const labels = useMailCounts(accountId, options).data?.labels.map((l) => l.name) ?? [];
```

- Replace the `<form role="search" …>…</form>` element with:

```tsx
      <div className="relative min-w-0 max-w-2xl flex-1">
        <form role="search" onSubmit={submit}>
          <div className="flex h-10 items-center gap-2 rounded-menu bg-sunken pl-3.5 pr-1 focus-within:bg-surface focus-within:ring-2 focus-within:ring-accent">
            <Icon name="search" className="text-[20px] text-ink-2" />
            <input
              aria-label={mail ? "Search mail" : "Search files"}
              aria-describedby="search-hint"
              placeholder={mail ? "Search mail: from:, larger:5M, older_than:1y" : "Search in this account"}
              className="min-w-0 flex-1 bg-transparent text-body outline-none placeholder:text-ink-2"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            {mail && onList && <IconButton icon="tune" label="Search options" aria-expanded={options} onClick={() => setOptions(!options)} />}
          </div>
          <span id="search-hint" className="sr-only">
            Type at least 2 characters, then press Enter.
          </span>
        </form>
        {options && mail && onList && (
          <SearchOptions
            text={q}
            view={mailView(params.get("view"))}
            labels={labels}
            onClose={() => setOptions(false)}
            onSearch={(text) => {
              setQ(text);
              setOptions(false);
              searchMail(text);
            }}
          />
        )}
      </div>
```

- [ ] **Step 6: Run the frontend checks**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(mail): a Search options form builds the search and reads it back

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 8: Pick all, and the actions of each view

**Files:**
- Modify: `frontend/src/api/types.ts` (`MailPickAll`, `OffloadStart`, `MailMessageInfo.state`)
- Modify: `frontend/src/api/rules.ts` (remove `DeleteMailResult`)
- Modify: `frontend/src/hooks/useMailOffload.ts` (`useRestoreMail` takes `view`; new `useOffloadOne`)
- Modify: `frontend/src/hooks/useRules.ts` (`useDeleteForever` takes the view; remove `useDeleteMail`)
- Modify: `frontend/src/components/mail/MailList.tsx` (Pick all checkbox; chips in every view)
- Modify: `frontend/src/components/mail/MailReader.tsx` (pick state, the bar per view)
- Modify: `frontend/src/components/mail/MailActions.tsx` (header per view, one-mail Offload, Restore or Delete forever)
- Modify: `frontend/src/components/mail/DeleteForeverDialog.tsx`
- Modify: `frontend/src/components/mail/OffloadMailDialog.tsx` (a pick-all start)
- Modify: `frontend/src/components/mail/MailRecovery.tsx` (Restore all sends the view)
- Modify: `frontend/src/components/mail/MailReader.test.tsx`, `frontend/src/components/mail/MailAi.test.tsx`, `frontend/src/hooks/useRules.test.tsx`, `frontend/src/pages/RecoveryPage.test.tsx`

**Interfaces:**
- Consumes:
  - `POST /api/mail/offloads/preview` and `POST /api/mail/offloads` with `{view, label?, q}` (Task 4)
  - `POST /api/mail/restore` with `{view, label?, q}` or `{thread_ids, view}` (Task 4)
  - `POST /api/mail/delete-forever` (Task 5)
  - `POST /api/mail/messages/{id}/offload` (Task 3)
  - `viewKind`, `mailView`, `mailThreadQuery(…, view)` (Task 6)
- Produces:
  - `type MailPickAll = { view: string; label?: string; q: string }`
  - `type OffloadStart = { q: string } | { message_ids: number[] } | { thread_ids: string[] } | MailPickAll`
  - `type PickState = "none" | "some" | "all"` and the `MailList` props `isPicked`, `onToggle`, `pickState`, `onPickAll`
  - `allPickedText(total: number | undefined, searching: boolean): string`
  - `RULE_FROM_AT_MOST = 10`
  - `useOffloadOne()` mutating `{ accountId, messageId }`
  - `useDeleteForever()` mutating `{ accountId, view, messageIds?, label?, q?, confirm }`
  - `useRestoreMail()` mutating `{ accountId, start?, offloadId?, view? }`
  - `DeleteForeverDialog` taking `start: { view: string; message_ids?: number[]; label?: string; q?: string }`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/mail/MailReader.test.tsx`:
- Delete these tests:
  - "offers Offload for a mail still in Gmail, and nothing for one whose file is missing"
  - "restores a view's whole list only when the search language can say that view"
  - "in the Local archive, picked threads restore from the archive or delete forever, not offload"
  - "deletes picked archived threads forever by their mails, after the typed count"
  - "keeps the picks when Delete forever… is cancelled"
  - "shows no rule chip in the Local archive, in the list or the reader"
  - "deletes the whole archive list forever with the search spelled out"
  - "asks for the totals again after Delete forever, so the header and count are not stale"
  - "has no Delete forever… for the whole list when a label filter is set"
  - "deletes picked threads after a plain confirm"
  - "offers Delete and Make a rule… for a mail in Gmail, and restore or delete forever for an archived one"
- In "restores picked threads together, then clears the picks", the expected body becomes `{ account_id: 1, thread_ids: ["t1", "t2"], view: "offloaded" }`.
- Rename the describe "AI rules and the local archive in the reader" to "AI rules in the reader". Its remaining tests ("shows a rule chip…" and "approves every suggestion…") stay as they are.
- Add:

```tsx
describe("Pick all and the actions of each view", () => {
  const TWO = { threads: [thread({ thread_id: "t1", subject: "One" }), thread({ thread_id: "t2", subject: "Two" })], next_cursor: null, total: null, total_bytes: null };
  const api = (more: Record<string, unknown> = {}, total = 12430) =>
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": TWO,
      "GET /api/mail/totals": { total, total_bytes: 1 },
      ...more,
    });
  const open = (route: string) => renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route });
  const bar = () => screen.getByRole("region", { name: "Picked mail" });

  it("has three states, and says what is picked", async () => {
    api();
    open("/mail?view=inbox&q=from%3Ashop");
    const all = await screen.findByRole("checkbox", { name: "Pick all" });
    expect(all).not.toBeChecked();
    await screen.findByText(/12,430 mails/);
    await userEvent.click(all);
    expect(all).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Pick One" })).toBeChecked();
    expect(within(bar()).getByText("All 12,430 mails in this search are picked.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick One" }));
    expect(all).toBePartiallyChecked();
    expect(within(bar()).getByText("1 thread picked")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Pick Two" })).toBeChecked();
    await userEvent.click(all);
    expect(all).toBeChecked();
    await userEvent.click(all);
    expect(all).not.toBeChecked();
    expect(screen.queryByRole("region", { name: "Picked mail" })).not.toBeInTheDocument();
  });

  it("says this view when there is no search, and The 1 mail for one", () => {
    expect(allPickedText(1204, false)).toBe("All 1,204 mails in this view are picked.");
    expect(allPickedText(1, true)).toBe("The 1 mail in this search is picked.");
    expect(allPickedText(undefined, true)).toBe("All mails in this search are picked.");
  });

  it("offers Offload and Make a rule… in Gmail views, and offloads a pick-all as the view and search", async () => {
    const { calls } = api({ "POST /api/mail/offloads/preview": { filter_text: "Inbox · from:shop", count: 2, bytes: 10, largest: [], oldest: [], excluded: [] } });
    open("/mail?view=inbox&q=from%3Ashop");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick all" }));
    expect(within(bar()).queryByRole("button", { name: "Restore to Gmail" })).not.toBeInTheDocument();
    expect(within(bar()).queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
    expect(within(bar()).getByRole("button", { name: "Make a rule…" })).toBeInTheDocument();
    await userEvent.click(within(bar()).getByRole("button", { name: "Offload" }));
    const dialog = screen.getByRole("dialog", { name: "Offload mail" });
    expect(within(dialog).getByText("Every mail of the list you picked them in.")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/offloads/preview")?.body).toEqual({ account_id: 1, view: "inbox", q: "from:shop" }));
  });

  it("restores a pick-all of Offloaded as the view and search", async () => {
    const { calls } = api({ "POST /api/mail/restore": { queued: "queued", count: 2 } });
    open("/mail?view=offloaded&label=Label_2");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick all" }));
    expect(within(bar()).queryByRole("button", { name: "Offload" })).not.toBeInTheDocument();
    await userEvent.click(within(bar()).getByRole("button", { name: "Restore to Gmail" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/restore")?.body).toEqual({ account_id: 1, view: "offloaded", label: "Label_2", q: "" }));
  });

  it("deletes a pick-all of Deleted in Gmail forever after the typed count", async () => {
    const { calls } = api({ "POST /api/mail/delete-forever": { deleted: 7, left: 0 } }, 7);
    open("/mail?view=deleted");
    await screen.findByText(/7 mails/);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick all" }));
    await userEvent.click(within(bar()).getByRole("button", { name: "Delete forever…" }));
    const dialog = screen.getByRole("dialog", { name: "Delete forever" });
    expect(dialog).toHaveTextContent("7 mails from Deleted in Gmail.");
    await userEvent.type(within(dialog).getByLabelText("Type 7 to delete forever"), "7");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete forever" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/delete-forever")?.body).toEqual({ account_id: 1, view: "deleted", q: "", confirm: 7 }));
    expect(await screen.findByText("Deleted 7 mails forever.")).toBeInTheDocument();
  });

  it("deletes picked Offloaded threads forever by their offloaded mails, and keeps the pick on Cancel", async () => {
    const { calls } = api({
      "GET /api/mail/threads/t1": [msg({ id: 4, state: "offloaded" }), msg({ id: 5, state: "offloaded" })],
      "POST /api/mail/delete-forever": { deleted: 2, left: 0 },
    });
    open("/mail?view=offloaded");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick One" }));
    await userEvent.click(within(bar()).getByRole("button", { name: "Delete forever…" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("checkbox", { name: "Pick One" })).toBeChecked();
    await userEvent.click(within(bar()).getByRole("button", { name: "Delete forever…" }));
    const dialog = await screen.findByRole("dialog");
    expect(calls.find((c) => c.path === "/api/mail/threads/t1")?.url.searchParams.get("view")).toBe("offloaded");
    await userEvent.type(within(dialog).getByLabelText("Type 2 to delete forever"), "2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete forever" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/delete-forever")?.body).toEqual({ account_id: 1, view: "offloaded", message_ids: [4, 5], confirm: 2 }));
  });

  it("has no Pick all in Suggested, and approves the picked suggestions", async () => {
    const CHIP = { id: 3, rule_name: "Newsletters", status: "suggested", how: "ai", why: "a digest" };
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1", subject: "One", decision: CHIP }), thread({ thread_id: "t2", subject: "Two", decision: { ...CHIP, id: 4 } })], next_cursor: null, total: 2, total_bytes: 2 },
      "POST /api/rule-decisions/approve": { applied: 1 },
    });
    open("/mail?view=suggested");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick Two" }));
    expect(screen.queryByRole("checkbox", { name: "Pick all" })).not.toBeInTheDocument();
    await userEvent.click(within(bar()).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/rule-decisions/approve")?.body).toEqual({ account_id: 1, ids: [4] }));
  });

  it("clears Pick all when the search changes", async () => {
    api();
    const r = open("/mail?view=inbox&q=a");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick all" }));
    r.unmount();
    open("/mail?view=inbox&q=b");
    expect(await screen.findByRole("checkbox", { name: "Pick all" })).not.toBeChecked();
  });

  it("offers Offload and Make a rule… for a mail in Gmail, and Restore or Delete forever… for one out of Gmail", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1" })], next_cursor: null, total: 1, total_bytes: 1 },
      "GET /api/mail/threads/t1": ({ url }: { url: URL }) =>
        url.searchParams.get("view") === "offloaded" ? [msg({ id: 2, state: "offloaded", gmail_url: null })] : [msg({ id: 1 })],
      "POST /api/mail/messages/1/offload": { offloaded: true, reason: null },
    });
    const first = open("/mail?thread=t1");
    const reader = await screen.findByRole("region", { name: "Open mail" });
    expect(await within(reader).findByRole("button", { name: "Make a rule…" })).toBeInTheDocument();
    expect(within(reader).queryByRole("button", { name: /^Delete/ })).not.toBeInTheDocument();
    await userEvent.click(within(reader).getByRole("button", { name: "Offload" }));
    const ask = screen.getByRole("dialog", { name: "Offload this mail?" });
    expect(within(ask).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.click(within(ask).getByRole("button", { name: "Offload" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/messages/1/offload")?.body).toEqual({ account_id: 1 }));
    expect(await screen.findByText("Offloaded. CleanSync keeps its copy in Offloaded.")).toBeInTheDocument();
    first.unmount();
    open("/mail?view=offloaded&thread=t1");
    const out = await screen.findByRole("region", { name: "Open mail" });
    expect(await within(out).findByRole("button", { name: "Delete forever…" })).toBeInTheDocument();
    expect(within(out).getByRole("button", { name: "Restore to Gmail" })).toBeInTheDocument();
    expect(within(out).queryByRole("button", { name: "Offload" })).not.toBeInTheDocument();
  });
});
```

Add `allPickedText` to the `./MailReader` import at the top of the file.

In `frontend/src/components/mail/MailAi.test.tsx`, replace the test "delete forever needs the typed count" with:

```tsx
  it("delete forever needs the typed count", async () => {
    const { calls } = mockApi({ "POST /api/mail/delete-forever": { deleted: 2, left: 0 } });
    renderApp(<DeleteForeverDialog account={ACCOUNT} start={{ view: "offloaded", message_ids: [1, 2] }} count={2} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Delete forever" });
    expect(dialog).toHaveTextContent("2 mails from Offloaded.");
    const go = within(dialog).getByRole("button", { name: "Delete forever" });
    expect(go).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Type 2 to delete forever"), "2");
    await userEvent.click(go);
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/delete-forever")?.body).toEqual({ account_id: 1, view: "offloaded", message_ids: [1, 2], confirm: 2 }));
  });
```

In `frontend/src/hooks/useRules.test.tsx`, remove `useDeleteMail` from the import. The test "deletes a rule with the mailbox in the query and mail by message or thread" keeps only its rule part, and is renamed "deletes a rule with the mailbox in the query". Delete the `"POST /api/mail/delete"` mock and the three lines that use `useDeleteMail`.

In `frontend/src/pages/RecoveryPage.test.tsx`, both expected restore bodies `{ account_id: 1, q: "is:deleted" }` become `{ account_id: 1, view: "deleted", q: "" }`.

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd frontend && npm test -- --run src/components/mail src/hooks/useRules.test.tsx src/pages/RecoveryPage.test.tsx`
Expected: FAIL. There is no "Pick all" checkbox, `allPickedText` isn't exported, and the old endpoints are still called.

- [ ] **Step 3: Types and hooks**

In `frontend/src/api/types.ts`:

```ts
/** Pick all (mail panel spec §6): every mail of the view and search, worked out by the server. */
export type MailPickAll = { view: string; label?: string; q: string };
export type OffloadStart = { q: string } | { message_ids: number[] } | { thread_ids: string[] } | MailPickAll;
```

`MailMessageInfo.state` becomes `"in_mail" | "offloaded" | "deleted_in_provider"`. In `frontend/src/api/rules.ts`, delete `DeleteMailResult`.

In `frontend/src/hooks/useMailOffload.ts`, replace `useRestoreMail` and add `useOffloadOne`:

```ts
/** Spec §5.5: restore never makes a second copy in Gmail, so it needs no typed confirmation.
 * `view` keeps a pick of threads to the mails of the view they were picked in. */
export function useRestoreMail() {
  const toast = useToast();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { accountId: number; start?: OffloadStart; offloadId?: number; view?: string }) =>
      api<{ queued: string; count: number }>("/api/mail/restore", {
        method: "POST",
        body: {
          account_id: b.accountId,
          ...(b.start ?? {}),
          ...(b.offloadId !== undefined ? { offload_id: b.offloadId } : {}),
          ...(b.view !== undefined ? { view: b.view } : {}),
        },
      }),
    onSuccess: ({ count }) => {
      toast(`Putting ${count.toLocaleString()} ${count === 1 ? "mail" : "mails"} back in Gmail. Sync activity shows when it's done.`);
      void qc.invalidateQueries({ queryKey: ["mail-status"] });
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
}

const MAIL_LISTS = ["mail-threads", "mail-counts", "mail-totals", "mail-thread", "mail-message", "rule-decisions"];

/** Offload one mail open in the reader (mail panel spec §8), through the same checks as a
 * rule's Offload. */
export function useOffloadOne() {
  const toast = useToast();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { accountId: number; messageId: number }) =>
      api<{ offloaded: boolean; reason: string | null }>(`/api/mail/messages/${b.messageId}/offload`, { method: "POST", body: { account_id: b.accountId } }),
    onSuccess: ({ offloaded, reason }) => {
      if (offloaded) toast("Offloaded. CleanSync keeps its copy in Offloaded.");
      else toast(`It wasn't offloaded: ${reason ?? "try again."}`, { tone: "error" });
      MAIL_LISTS.forEach((k) => void qc.invalidateQueries({ queryKey: [k] }));
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
}
```

In `frontend/src/hooks/useRules.ts`, delete `useDeleteMail` and its `DeleteMailResult` import, and replace `useDeleteForever` with:

```ts
// ---- Delete forever ----
/** Removes CleanSync's copy of mail already out of Gmail (mail panel spec §7): picked mails,
 * or a pick-all as the view, label and search. `confirm` is the typed count. */
export const useDeleteForever = () =>
  useChange((b: Acc & { view: string; messageIds?: number[]; label?: string; q?: string; confirm: number }) =>
    post<{ deleted: number; left: number }>("/api/mail/delete-forever", {
      account_id: b.accountId,
      view: b.view,
      ...(b.messageIds ? { message_ids: b.messageIds } : {}),
      ...(b.label ? { label: b.label } : {}),
      ...(b.q !== undefined ? { q: b.q } : {}),
      confirm: b.confirm,
    }),
  );
```

- [ ] **Step 4: The list's Pick all**

In `frontend/src/components/mail/MailList.tsx`:
- Import `useEffect` and `useRef` from `react`.
- Remove the `chips` prop from `Row` and its doc comment, and use `const chip = t.decision;`.
- Add above `MailList`:

```tsx
/** The checkbox at the top of the list (mail panel spec §6): empty, mixed (some picked by
 * hand) or ticked (every mail of the view and search). */
export type PickState = "none" | "some" | "all";

function PickAllBox({ state, onChange }: { state: PickState; onChange: () => void }) {
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (box.current) box.current.indeterminate = state === "some";
  }, [state]);
  return (
    <label className="flex items-center gap-3 border-b border-line px-4 py-2 text-table text-ink-2">
      <input ref={box} type="checkbox" className="h-4 w-4 accent-accent" aria-label="Pick all" checked={state === "all"} onChange={onChange} />
      <span aria-hidden="true">Pick all</span>
    </label>
  );
}
```

- `MailList`'s props become:

```tsx
/** `isPicked` and `onToggle` turn on picking threads; `onPickAll` adds the Pick all checkbox
 * (not in Suggested, where Approve all covers the list). */
export function MailList({
  accountId,
  view,
  label,
  q,
  thread,
  isPicked,
  onToggle,
  pickState = "none",
  onPickAll,
}: {
  accountId: number;
  view: string;
  label: string;
  q: string;
  thread: string;
  isPicked?: (threadId: string) => boolean;
  onToggle?: (threadId: string) => void;
  pickState?: PickState;
  onPickAll?: () => void;
}) {
```

- Just inside the returned `<div>`, before `<ul aria-label="Mail">`, add `{onPickAll && <PickAllBox state={pickState} onChange={onPickAll} />}`. In the `Row` call, drop `chips={…}` and use `picked={isPicked?.(t.thread_id) ?? false}`.

- [ ] **Step 5: The reader**

Replace `frontend/src/components/mail/MailReader.tsx` with:

```tsx
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { friendlyError } from "../../api/client";
import { mailThreadQuery, useMailCounts, useMailThreads, useMailTotals } from "../../api/queries";
import type { Account, MailPickAll } from "../../api/types";
import { useRestoreMail } from "../../hooks/useMailOffload";
import { useApprove } from "../../hooks/useRules";
import { formatBytes } from "../../lib/format";
import { mailHref, mailView, viewKind, viewTitle } from "../../lib/mail";
import { OffloadBatches } from "../../pages/MailOffloadPage";
import { useToast } from "../Toasts";
import { Button } from "../ui";
import { DeleteForeverDialog } from "./DeleteForeverDialog";
import { MailHeaderActions } from "./MailActions";
import { MailList, type PickState } from "./MailList";
import { isNarrow, MailThread } from "./MailThread";
import { MakeRuleDialog } from "./MakeRuleDialog";
import { OffloadMailDialog } from "./OffloadMailDialog";

/** "Make a rule…" drafts from at most this many picked mails (mail panel spec §6). */
export const RULE_FROM_AT_MOST = 10;
type PickedAction = "offload" | "rule" | null;
type Forever = { message_ids: number[] } | "all";

/** What the bar says after Pick all (mail panel spec §6). */
export function allPickedText(total: number | undefined, searching: boolean): string {
  const where = searching ? "this search" : "this view";
  if (total === undefined) return `All mails in ${where} are picked.`;
  if (total === 1) return `The 1 mail in ${where} is picked.`;
  return `All ${total.toLocaleString()} mails in ${where} are picked.`;
}

/** Spec §6.1: the list beside the open mail; on phones one column at a time. Mail panel
 * spec §6: Pick all, and the actions of the view on picked mail. */
export function MailReader({ account }: { account: Account }) {
  const [params] = useSearchParams();
  const view = mailView(params.get("view"));
  const label = params.get("label") ?? "";
  const q = params.get("q") ?? "";
  const thread = params.get("thread") ?? "";
  const batches = view === "offloaded" && params.get("tab") === "batches";
  const kind = viewKind(view);
  const labelName = useMailCounts(account.id, label !== "").data?.labels.find((l) => l.id === label)?.name;
  const pages = useMailThreads(account.id, { view, label, q }).data?.pages;
  const rows = pages?.flatMap((p) => p.threads) ?? [];
  const totals = useMailTotals(account.id, { view, label, q }).data;
  const list = useRef<HTMLElement>(null);
  const backFrom = useRef<string | null>(null); // the thread "Back to the list" left
  useEffect(() => {
    const from = backFrom.current;
    if (thread || from === null) return;
    backFrom.current = null;
    if (isNarrow()) list.current?.querySelector<HTMLElement>(`[data-thread="${CSS.escape(from)}"]`)?.focus();
  }, [thread]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [all, setAll] = useState(false);
  const [pickedAction, setPickedAction] = useState<PickedAction>(null);
  const [forever, setForever] = useState<Forever | null>(null);
  const [finding, setFinding] = useState(false); // reading which mails the picked threads hold
  const restore = useRestoreMail();
  const approve = useApprove();
  const qc = useQueryClient();
  const toast = useToast();
  const pickAll: MailPickAll = { view, ...(label ? { label } : {}), q };
  const clearPicks = () => {
    setPicked(new Set());
    setAll(false);
  };
  // Picks belong to one list: a new view, label or search starts with none.
  useEffect(() => {
    setPicked(new Set());
    setAll(false);
  }, [view, label, q]);
  const pickState: PickState = all ? "all" : picked.size > 0 ? "some" : "none";
  const isPicked = (id: string) => all || picked.has(id);
  const toggle = (id: string) => {
    // Unticking one row after Pick all leaves a pick by hand: the rows on screen but that one.
    const next = all ? new Set(rows.map((r) => r.thread_id)) : new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setAll(false);
    setPicked(next);
  };
  const togglePickAll = () => {
    if (all) return clearPicks();
    setPicked(new Set());
    setAll(true);
  };
  const pickedRows = rows.filter((t) => isPicked(t.thread_id));
  const suggestedIds = rows.flatMap((t) => (t.decision?.status === "suggested" ? [t.decision.id] : []));
  const pickedSuggestions = pickedRows.flatMap((t) => (t.decision?.status === "suggested" ? [t.decision.id] : []));
  const ruleFrom = pickedRows.map((t) => t.message_id).slice(0, RULE_FROM_AT_MOST);
  // Delete forever takes mails, not threads: read which mails of this view the picked threads hold.
  const pickForever = () => {
    if (all) return setForever("all");
    if (finding) return;
    setFinding(true);
    Promise.all([...picked].map((id) => qc.fetchQuery(mailThreadQuery(account.id, id, view))))
      .then((threads) => setForever({ message_ids: threads.flat().map((m) => m.id) }))
      .catch((e: unknown) => toast(friendlyError(e), { tone: "error" }))
      .finally(() => setFinding(false));
  };
  const restorePicked = () =>
    restore.mutate(all ? { accountId: account.id, start: pickAll } : { accountId: account.id, start: { thread_ids: [...picked] }, view }, { onSuccess: clearPicks });
  const searching = q.trim() !== "";
  const title = searching ? "Search results" : viewTitle(view, label ? (labelName ?? label) : null);
  const scope = label ? (labelName ?? label) : view !== "all" ? viewTitle(view) : null;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-4 sm:px-6">
        <h1 className="text-title font-semibold">{title}</h1>
        {searching && <span className="text-table text-ink-2">for {q}</span>}
        {totals && (
          <span className="text-table text-ink-2 tabular">
            {totals.total.toLocaleString()} {totals.total === 1 ? "mail" : "mails"} · {formatBytes(totals.total_bytes)}
          </span>
        )}
        {view === "suggested" && (
          <Button tone="primary" disabled={suggestedIds.length === 0 || approve.isPending} onClick={() => approve.mutate({ accountId: account.id, ids: suggestedIds })}>
            Approve all
          </Button>
        )}
        <div className="ml-auto flex flex-wrap gap-2">
          <MailHeaderActions account={account} q={q} view={view} label={label} scope={scope} />
        </div>
      </div>
      {view === "offloaded" && (
        <nav aria-label="Offloaded" className="flex gap-4 px-4 pb-2 text-table font-semibold sm:px-6">
          <Link to={mailHref({ view: "offloaded" })} aria-current={batches ? undefined : "page"} className={batches ? "text-ink-2" : "text-accent"}>
            Mail
          </Link>
          <Link to={mailHref({ view: "offloaded", tab: "batches" })} aria-current={batches ? "page" : undefined} className={batches ? "text-accent" : "text-ink-2"}>
            Batches
          </Link>
        </nav>
      )}
      {pickState !== "none" && !batches && (
        <section aria-label="Picked mail" className="flex flex-wrap items-center gap-3 border-t border-line bg-sunken px-4 py-2 sm:px-6">
          <span className="text-table">
            {all ? allPickedText(totals?.total, searching) : `${picked.size.toLocaleString()} ${picked.size === 1 ? "thread" : "threads"} picked`}
          </span>
          {kind === "gmail" && (
            <>
              <Button onClick={() => setPickedAction("offload")}>Offload</Button>
              <Button onClick={() => setPickedAction("rule")}>Make a rule…</Button>
            </>
          )}
          {kind === "out" && (
            <>
              <Button disabled={restore.isPending} onClick={restorePicked}>
                Restore to Gmail
              </Button>
              <Button tone="danger" disabled={finding || (all && totals === undefined)} onClick={pickForever}>
                Delete forever…
              </Button>
            </>
          )}
          {kind === "suggested" && (
            <Button
              tone="primary"
              disabled={pickedSuggestions.length === 0 || approve.isPending}
              onClick={() => approve.mutate({ accountId: account.id, ids: pickedSuggestions }, { onSuccess: clearPicks })}
            >
              Approve
            </Button>
          )}
          <Button tone="quiet" onClick={clearPicks}>
            Clear
          </Button>
        </section>
      )}
      {pickedAction === "offload" && (
        <OffloadMailDialog account={account} start={all ? pickAll : { thread_ids: [...picked] }} onClose={() => setPickedAction(null)} />
      )}
      {pickedAction === "rule" && <MakeRuleDialog account={account} messageIds={ruleFrom} onClose={() => setPickedAction(null)} />}
      {forever && (
        <DeleteForeverDialog
          account={account}
          start={forever === "all" ? pickAll : { view, message_ids: forever.message_ids }}
          count={forever === "all" ? (totals?.total ?? 0) : forever.message_ids.length}
          onDone={clearPicks}
          onClose={() => setForever(null)}
        />
      )}
      {batches ? (
        <div className="min-h-0 flex-1 overflow-y-auto border-t border-line p-4 sm:p-6">
          <OffloadBatches accountId={account.id} />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 border-t border-line">
          <section ref={list} aria-label="Mail list" className={`min-w-0 overflow-y-auto xl:w-[440px] xl:shrink-0 xl:border-r xl:border-line ${thread ? "hidden xl:block" : "flex-1 xl:flex-none"}`}>
            <MailList
              accountId={account.id}
              view={view}
              label={label}
              q={q}
              thread={thread}
              isPicked={isPicked}
              onToggle={toggle}
              pickState={pickState}
              onPickAll={kind === "suggested" ? undefined : togglePickAll}
            />
          </section>
          <section aria-label="Open mail" className={`min-w-0 flex-1 overflow-y-auto ${thread ? "" : "hidden xl:block"}`}>
            {thread ? <MailThread account={account} threadId={thread} onBack={() => (backFrom.current = thread)} /> : <p className="p-6 text-ink-2">Pick a mail to read it.</p>}
          </section>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 6: The header and one mail's actions**

Replace `frontend/src/components/mail/MailActions.tsx` with:

```tsx
import { useState } from "react";

import type { Account, MailMessageInfo } from "../../api/types";
import { useOffloadOne, useRestoreMail } from "../../hooks/useMailOffload";
import { runRulesStart, viewKind } from "../../lib/mail";
import { Button, Dialog } from "../ui";
import { DeleteForeverDialog } from "./DeleteForeverDialog";
import { MakeRuleDialog } from "./MakeRuleDialog";
import { OffloadMailDialog } from "./OffloadMailDialog";
import { RuleChip } from "./RuleChip";
import { RunRulesDialog, type RunRulesStart } from "./RunRulesDialog";

/** The page's main action (spec §9: the deep indigo button), in the Gmail views only. Picked
 * mail has its own actions in the bar (mail panel spec §6). `scope` names the view or label
 * on screen, which the offload filter doesn't carry over. */
export function MailHeaderActions({
  account,
  q,
  view,
  label = "",
  scope = null,
}: {
  account: Account;
  q: string;
  view: string;
  label?: string;
  scope?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [rules, setRules] = useState<RunRulesStart | null>(null);
  if (viewKind(view) !== "gmail") return null;
  const rulesStart = runRulesStart(view, label, scope, q);
  return (
    <>
      {rulesStart && <Button onClick={() => setRules(rulesStart)}>Run rules on…</Button>}
      <Button tone="main" onClick={() => setOpen(true)}>
        Offload mail…
      </Button>
      {open && <OffloadMailDialog account={account} start={{ q }} scope={scope} onClose={() => setOpen(false)} />}
      {rules && <RunRulesDialog account={account} start={rules} onClose={() => setRules(null)} />}
    </>
  );
}

/** Offload one mail open in the reader (mail panel spec §8): checked, moved to Gmail's trash,
 * kept here in Offloaded. A plain confirm, as Delete had. */
export function OffloadOneDialog({ account, messageId, onClose }: { account: Account; messageId: number; onClose: () => void }) {
  const offload = useOffloadOne();
  return (
    <Dialog
      title="Offload this mail?"
      onClose={onClose}
      footer={
        <>
          <Button data-autofocus onClick={onClose}>
            Cancel
          </Button>
          <Button tone="main" disabled={offload.isPending} onClick={() => offload.mutate({ accountId: account.id, messageId }, { onSuccess: onClose })}>
            Offload
          </Button>
        </>
      }
    >
      <p>CleanSync checks its copy, moves the mail to Gmail's trash and keeps the copy here, in Offloaded. You can restore it to Gmail at any time.</p>
    </Dialog>
  );
}

type Open = "offload" | "rule" | "forever" | null;

/** Mail panel spec §8: a mail in Gmail offers Offload and Make a rule…; one out of Gmail
 * (offloaded, or deleted in Gmail) is read-only, with Restore to Gmail and Delete forever…. */
export function MessageMailActions({ account, message }: { account: Account; message: MailMessageInfo }) {
  const [open, setOpen] = useState<Open>(null);
  const restore = useRestoreMail();
  if (message.file_missing) return null; // nothing here to offload from or restore with
  const ids = [message.id];
  const close = () => setOpen(null);
  const view = message.state === "deleted_in_provider" ? "deleted" : "offloaded";
  return (
    <>
      {message.decision && <RuleChip accountId={account.id} chip={message.decision} />}
      {message.state === "in_mail" ? (
        <>
          <Button onClick={() => setOpen("offload")}>Offload</Button>
          <Button onClick={() => setOpen("rule")}>Make a rule…</Button>
        </>
      ) : (
        <>
          <Button disabled={restore.isPending} onClick={() => restore.mutate({ accountId: account.id, start: { message_ids: ids } })}>
            Restore to Gmail
          </Button>
          <Button onClick={() => setOpen("forever")}>Delete forever…</Button>
        </>
      )}
      {open === "offload" && <OffloadOneDialog account={account} messageId={message.id} onClose={close} />}
      {open === "rule" && <MakeRuleDialog account={account} messageIds={ids} onClose={close} />}
      {open === "forever" && <DeleteForeverDialog account={account} start={{ view, message_ids: ids }} count={1} onClose={close} />}
    </>
  );
}
```

- [ ] **Step 7: The dialogs and Recovery**

Replace the body of `frontend/src/components/mail/DeleteForeverDialog.tsx` from its doc comment to the end with:

```tsx
const PLACES: Record<string, string> = { offloaded: "Offloaded", deleted: "Deleted in Gmail" };

/** Mail panel spec §7: removes CleanSync's copy of mail already out of Gmail. Picked mails
 * (`message_ids`), or a pick-all as the view, label and search. `count` is how many that is;
 * the person types it, and the server counts again. `onDone` runs only when mail was deleted. */
export function DeleteForeverDialog({
  account,
  start,
  count,
  onClose,
  onDone,
}: {
  account: Account;
  start: { view: string; message_ids?: number[]; label?: string; q?: string };
  count: number;
  onClose: () => void;
  onDone?: () => void;
}) {
  const [typed, setTyped] = useState("");
  const forever = useDeleteForever();
  const toast = useToast();
  const ok = count > 0 && parseTyped(typed) === count;
  const go = () =>
    forever.mutate(
      {
        accountId: account.id,
        view: start.view,
        messageIds: start.message_ids,
        label: start.label,
        q: start.message_ids ? undefined : (start.q ?? ""),
        confirm: count,
      },
      {
        onSuccess: ({ deleted }) => {
          toast(`Deleted ${mails(deleted)} forever.`);
          onDone?.();
          onClose();
        },
        onError: () => setTyped(""),
      },
    );
  return (
    <Dialog
      title="Delete forever"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button tone="danger" disabled={!ok || forever.isPending} onClick={go}>
            Delete forever
          </Button>
        </>
      }
    >
      <p>
        {mails(count)} from {PLACES[start.view] ?? start.view}.
      </p>
      <p className="text-ink-2">This removes CleanSync's own copy. The mail is already gone from Gmail, so afterwards it exists nowhere. This can't be undone.</p>
      {count > 0 && (
        <Field label={`Type ${count.toLocaleString()} to delete forever`}>
          <input className={`${inputClass} w-40`} inputMode="numeric" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
        </Field>
      )}
    </Dialog>
  );
}
```

In `frontend/src/components/mail/OffloadMailDialog.tsx`:
- `const fixed = !("q" in start);` becomes `const fixed = !("q" in start) || "view" in start; // picked mail, or a pick-all`.
- The `fixed` text becomes:

```tsx
        <p>{"view" in start ? "Every mail of the list you picked them in." : "thread_ids" in start ? "The picked threads." : "The picked mails."}</p>
```

In `frontend/src/components/mail/MailRecovery.tsx`, the "Put back" button's `restore.mutate({ accountId, start: { q: "is:deleted" } })` becomes `restore.mutate({ accountId, start: { view: "deleted", q: "" } })`.

- [ ] **Step 8: Run the frontend checks**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck && grep -rn "local_archive\|archive/delete-forever\|/api/mail/delete\"\|useDeleteMail\|Local archive" src`
Expected: tests, lint and typecheck PASS, and the grep finds nothing.

- [ ] **Step 9: Commit**

```bash
git add frontend/src
git commit -m "feat(mail): Pick all, and Offload, Restore, Delete forever and Approve per view

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 9: The rule form's actions: Offload, and Delete forever listed

**Files:**
- Modify: `frontend/src/api/rules.ts` (`RuleAction`, `describeAction`)
- Modify: `frontend/src/components/rules/RuleForm.tsx`
- Modify: `frontend/src/components/rules/RuleForm.test.tsx`, `frontend/src/hooks/useRules.test.tsx`

**Interfaces:**
- Consumes: the server's action kinds from Task 3 (`offload`; `delete_forever` refused).
- Produces:
  - `type RuleAction = { kind: "archive" | "mark_read" | "star" | "offload" | "delete_forever" } | { kind: "label"; label: string }`
  - `describeAction({ kind: "offload" })` gives `"Offload"`; `describeAction({ kind: "delete_forever" })` gives `"Delete forever"`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/rules/RuleForm.test.tsx`, replace the first test ("fills from a draft, keeps Delete on its own, and saves the right body") with:

```tsx
  it("fills from a draft, keeps Offload on its own, and saves the right body", async () => {
    const { calls } = mockApi({ "POST /api/rules": SAVED });
    const onSaved = vi.fn();
    renderApp(<RuleForm accountId={1} initial={DRAFT} onSaved={onSaved} onCancel={() => {}} />);
    expect(screen.getByLabelText("Name")).toHaveValue("Old newsletters");
    expect(screen.getByLabelText("Which mail is this for?")).toHaveValue("Newsletters I signed up for");
    expect(screen.getByRole("checkbox", { name: "Archive" })).toBeChecked();
    expect(screen.getByLabelText("From")).toHaveValue("news@example.com");
    expect(screen.getByLabelText("How these work with the description")).toHaveValue("fixed_then_ai");

    await userEvent.click(screen.getByRole("checkbox", { name: "Offload" }));
    expect(screen.getByRole("checkbox", { name: "Archive" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Star" })).not.toBeChecked();
    expect(screen.getByText(/Offload checks CleanSync's copy, moves the mail out of Gmail and keeps the copy in Offloaded/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Mark read" }));
    expect(screen.getByRole("checkbox", { name: "Offload" })).not.toBeChecked();
    await userEvent.click(screen.getByRole("checkbox", { name: "Mark read" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Offload" }));

    await userEvent.clear(screen.getByLabelText("From"));
    await userEvent.type(screen.getByLabelText("From"), " a@example.com , ,b.example.com ");
    await userEvent.type(screen.getByLabelText("Subject contains"), "digest, weekly");
    await userEvent.click(screen.getByRole("radio", { name: "Automatic" }));
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "/api/rules")?.body).toEqual({
        account_id: 1,
        name: "Old newsletters",
        description: "Newsletters I signed up for",
        conditions: { senders_in: ["a@example.com", "b.example.com"], subject_contains: ["digest", "weekly"] },
        combine: "fixed_then_ai",
        actions: [{ kind: "offload" }],
        mode: "automatic",
        enabled: true,
      }),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("lists the actions in order, with Delete forever shown but not yet available", () => {
    mockApi({});
    renderApp(<RuleForm accountId={1} initial={DRAFT} onSaved={() => {}} onCancel={() => {}} />);
    const what = screen.getByRole("group", { name: "What it does" });
    expect(within(what).getAllByRole("checkbox").map((c) => c.getAttribute("aria-label") ?? c.closest("label")?.textContent?.trim())).toEqual([
      "Archive",
      "Label",
      "Mark read",
      "Star",
      "Offload",
      "Delete forever",
    ]);
    const forever = within(what).getByRole("checkbox", { name: "Delete forever" });
    expect(forever).toBeDisabled();
    expect(forever).toHaveAccessibleDescription("Needs full Gmail access (coming in this release)");
    expect(within(what).queryByRole("checkbox", { name: "Delete" })).not.toBeInTheDocument();
  });
```

Add `within` to the `@testing-library/react` import.

In `frontend/src/hooks/useRules.test.tsx`, in the test that checks `describeAction`, add:

```ts
    expect(describeAction({ kind: "offload" })).toBe("Offload");
    expect(describeAction({ kind: "delete_forever" })).toBe("Delete forever");
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd frontend && npm test -- --run src/components/rules/RuleForm.test.tsx src/hooks/useRules.test.tsx`
Expected: FAIL. There is no "Offload" checkbox, and `describeAction` doesn't know `offload`.

- [ ] **Step 3: Types and words**

In `frontend/src/api/rules.ts`:

```ts
export type RuleAction = { kind: "archive" | "mark_read" | "star" | "offload" | "delete_forever" } | { kind: "label"; label: string };
```

```ts
const ACTION_WORDS = { archive: "Archive", mark_read: "Mark read", star: "Star", offload: "Offload", delete_forever: "Delete forever" } as const;
```

- [ ] **Step 4: The form**

In `frontend/src/components/rules/RuleForm.tsx`:
- Replace the `simple` and `del` state lines with:

```tsx
  const [simple, setSimple] = useState<Set<Simple>>(new Set(initial.actions.flatMap((a) => (SIMPLE.some(([k]) => k === a.kind) ? [a.kind as Simple] : []))));
```

```tsx
  const [off, setOff] = useState(initial.actions.some((a) => a.kind === "offload"));
```

- Replace `pickDelete`, `pickSimple`, `pickLabel` and `actions` with:

```tsx
  // Offload stands alone: ticking it clears the others, and ticking another clears it.
  const pickOffload = (on: boolean) => {
    setOff(on);
    if (on) {
      setSimple(new Set());
      setLabelOn(false);
    }
  };
  const pickSimple = (kind: Simple, on: boolean) => {
    const next = new Set(simple);
    if (on) next.add(kind);
    else next.delete(kind);
    setSimple(next);
    if (on) setOff(false);
  };
  const pickLabel = (on: boolean) => {
    setLabelOn(on);
    if (on) setOff(false);
  };

  const actions: RuleAction[] = off
    ? [{ kind: "offload" }]
    : [...SIMPLE.filter(([k]) => simple.has(k)).map(([kind]) => ({ kind })), ...(labelOn ? [{ kind: "label" as const, label: labelName.trim() }] : [])];
```

- Replace the `<fieldset>` "What it does" block, up to and including its hint paragraph, with:

```tsx
      <fieldset>
        <legend className="mb-1 text-table font-medium text-ink-2">What it does</legend>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {/* Mail panel spec §8: Archive, Label, Mark read, Star, Offload, Delete forever. */}
          {[SIMPLE[0]].map(([kind, label]) => (
            <label key={kind} className="flex items-center gap-2">
              <input type="checkbox" checked={simple.has(kind)} onChange={(e) => pickSimple(kind, e.target.checked)} />
              {label}
            </label>
          ))}
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={labelOn} onChange={(e) => pickLabel(e.target.checked)} />
            Label
          </label>
          {SIMPLE.slice(1).map(([kind, label]) => (
            <label key={kind} className="flex items-center gap-2">
              <input type="checkbox" checked={simple.has(kind)} onChange={(e) => pickSimple(kind, e.target.checked)} />
              {label}
            </label>
          ))}
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={off} aria-describedby={`${id}-off`} onChange={(e) => pickOffload(e.target.checked)} />
            Offload
          </label>
          {/* Round 3 of 0.5.0 turns this on, with the full Gmail permission. */}
          <label className="flex items-center gap-2 text-ink-2">
            <input type="checkbox" checked={false} disabled aria-describedby={`${id}-forever`} onChange={() => undefined} />
            Delete forever
          </label>
        </div>
        {labelOn && (
          <div className="mt-2 max-w-xs">
            <Field label="Label name">
              <input className={inputClass} value={labelName} onChange={(e) => setLabelName(e.target.value)} />
            </Field>
          </div>
        )}
        <p id={`${id}-off`} className="mt-1 text-meta text-ink-2">
          Offload checks CleanSync's copy, moves the mail out of Gmail and keeps the copy in Offloaded. You can restore it later.
        </p>
        <p id={`${id}-forever`} className="mt-1 text-meta text-ink-2">
          Needs full Gmail access (coming in this release)
        </p>
      </fieldset>
```

The `SIMPLE` list stays `[["archive", "Archive"], ["mark_read", "Mark read"], ["star", "Star"]]`. The form shows Label between Archive and Mark read, so `SIMPLE[0]` and `SIMPLE.slice(1)` place it there.

- [ ] **Step 5: Run the frontend checks**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: PASS. If `Rules.test.tsx` or `MailAi.test.tsx` gives a rule `{ kind: "delete" }`, change it to `{ kind: "offload" }` and the expected words to "Offload".

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(rules): the rule form offers Offload, and lists Delete forever for later in this release

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: The browser test and the docs

**Files:**
- Modify: `backend/app/devtools/fake_gmail.py` (a `delete` command; the saved-file stamp)
- Modify: `backend/app/services/mail/adapters.py` (read the fake mailbox again when its file changed)
- Create: `backend/tests/test_fake_gmail_cli.py`
- Modify: `frontend/e2e/helpers.ts` (`fakeGmailDelete`)
- Modify: `frontend/e2e/rules.spec.ts` (steps 5 and 6 replaced)
- Modify: `docs/mail-backup.md`, `docs/mail-rules.md`, `docs/limitations.md`

**Interfaces:**
- Consumes: everything above, through the real app.
- Produces:
  - `python -m app.devtools.fake_gmail delete --file PATH --subject TEXT`: removes every mail with that subject and records `messagesDeleted` in the fake history. It exits 0 when it removed one, 1 when none matched.
  - `FakeGmail.changed_on_disk() -> bool`
  - `fakeGmailDelete(subject: string): boolean` in `e2e/helpers.ts`. It returns false (the step is skipped) when neither `E2E_CONTAINER` nor `E2E_FAKE_GMAIL` is set, and in CI it throws.

- [ ] **Step 1: Write the failing backend tests**

```python
# backend/tests/test_fake_gmail_cli.py
"""The fake Gmail can be changed from outside, as the owner would in Gmail (the browser test
deletes a mail this way to see it under Deleted in Gmail)."""

import httpx

from app.devtools.fake_gmail import FakeGmail, main, make_mail
from app.services.accounts.service import PRETEND_PREFIX
from app.services.google.client import GoogleClient
from app.services.mail.adapters import MailAdapters


def test_delete_by_subject_records_it_in_the_history(tmp_path):
    path = tmp_path / "alice.json"
    fake = FakeGmail(path=path)
    keep = fake.add(make_mail("Keep me"))
    fake.add(make_mail("Remove me"))
    start = fake.history_id
    assert not fake.changed_on_disk()
    assert main(["delete", "--file", str(path), "--subject", "Remove me"]) == 0
    again = FakeGmail.load(path)
    assert list(again.messages) == [keep]
    assert any("messagesDeleted" in h and int(h["id"]) > start for h in again.history)
    assert fake.changed_on_disk()
    assert main(["delete", "--file", str(path), "--subject", "Not there"]) == 1


def test_the_pretend_mailbox_is_read_again_after_it_changed_on_disk(layout, make_account):
    acc = make_account(remote_name=f"{PRETEND_PREFIX}alice")
    adapters = MailAdapters(
        google=GoogleClient(budget=None, pacer=None, http_factory=lambda: httpx.AsyncClient()),
        tokens=None,
        layout=layout,
        allow_pretend=True,
    )
    path = adapters.fake_path(acc.email)
    FakeGmail(email=acc.email, path=path).add(make_mail("Remove me"))
    adapters.for_account(acc)
    first = adapters._fakes[acc.email]
    first.add(make_mail("Saved by the server"))  # the server's own saves don't count
    adapters.for_account(acc)
    assert adapters._fakes[acc.email] is first
    assert main(["delete", "--file", str(path), "--subject", "Remove me"]) == 0
    adapters.for_account(acc)
    now = adapters._fakes[acc.email]
    assert now is not first and len(now.messages) == 1
```

If `GoogleClient` needs other arguments, build it the way `tests/mail_helpers.py::fake_adapter` does.

- [ ] **Step 2: Run them to check they fail**

Run: `cd backend && uv run pytest tests/test_fake_gmail_cli.py -q`
Expected: FAIL with `ImportError: cannot import name 'main'`.

- [ ] **Step 3: The fake Gmail**

In `backend/app/devtools/fake_gmail.py`:
- Add `import argparse` to the imports. `email` is already importable through `import email.utils`.
- Add a field to `FakeGmail` after `_next`: `_saved: tuple[int, int] | None = None  # the file as this object last wrote or read it`.
- Add these methods in the "saving" part:

```python
    def disk_stamp(self) -> tuple[int, int] | None:
        """When and how big the saved file is now, or None without one."""
        try:
            st = self.path.stat() if self.path is not None else None
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size) if st is not None else None

    def changed_on_disk(self) -> bool:
        """Something else (the delete command) changed the file since this object saved it."""
        return self.path is not None and self.disk_stamp() != self._saved
```

- At the end of `save()`, after `os.replace(tmp, self.path)`, add `self._saved = self.disk_stamp()`. In `load()`, before `return fake` at the end, add `fake._saved = fake.disk_stamp()`.
- At the end of the module, add:

```python
def _subject(raw: bytes) -> str:
    return str(email.message_from_bytes(raw).get("Subject") or "").strip()


def main(argv: list[str] | None = None) -> int:
    """Development and the browser test only: change the pretend mailbox from outside, as the
    owner would in Gmail. `delete --file F --subject S` deletes every mail with subject S."""
    parser = argparse.ArgumentParser(prog="python -m app.devtools.fake_gmail")
    commands = parser.add_subparsers(dest="command", required=True)
    delete = commands.add_parser("delete", help="delete mail by subject, as if deleted in Gmail")
    delete.add_argument("--file", required=True, type=Path)
    delete.add_argument("--subject", required=True)
    args = parser.parse_args(argv)
    fake = FakeGmail.load(args.file)
    gone = [m.id for m in fake.messages.values() if _subject(m.raw) == args.subject]
    for mid in gone:
        fake.remove(mid)
    print(f"deleted {len(gone)}")
    return 0 if gone else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

In `backend/app/services/mail/adapters.py`, in `for_account`, the condition `if fake is None:` becomes `if fake is None or fake.changed_on_disk():`.

Run: `cd backend && uv run pytest tests/test_fake_gmail_cli.py tests/test_mail_api.py -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 4: The e2e helper**

In `frontend/e2e/helpers.ts`, add after `fakeGmailTrash`:

```ts
/** Deletes mail in the pretend Gmail by subject, as if the owner deleted it on their phone.
 * Like fakeGmailTrash it needs E2E_CONTAINER or E2E_FAKE_GMAIL (with E2E_FAKE_GMAIL the
 * backend's own Python runs it, from ../backend); without either it returns false and the
 * test skips that part. */
export function fakeGmailDelete(subject: string): boolean {
  const name = process.env.E2E_CONTAINER;
  const file = process.env.E2E_FAKE_GMAIL;
  if (!name && !file) {
    if (process.env.CI) throw new Error("E2E_CONTAINER must name the container in CI, or the Deleted in Gmail step is skipped");
    return false;
  }
  const args = ["-m", "app.devtools.fake_gmail", "delete", "--file", name ? "/drivesync/app/data/fake-gmail/alice@example.com.json" : file!, "--subject", subject];
  if (name) execFileSync("docker", ["exec", name, "python", ...args]);
  else execFileSync("uv", ["run", "python", ...args], { cwd: process.env.E2E_BACKEND_DIR ?? "../backend" });
  return true;
}
```

- [ ] **Step 5: The browser test**

In `frontend/e2e/rules.spec.ts`:
- Import `fakeGmailDelete` with the other helpers.
- Rename the first test to `"a rule written in a sentence previews, applies, undoes; a search offloads, restores and deletes forever"`.
- Replace steps 5 and 6 (from the comment `// 5. Delete a mail` up to the line before `expect(problems, …)`) with:

```ts
  // 5. Build a search with the form in the Inbox, pick all of it, and offload it.
  await page.goto("/mail?view=inbox");
  await page.getByRole("button", { name: "Search options" }).click();
  const options = page.getByRole("region", { name: "Search options" });
  await options.getByLabel("Has the words").fill("weekend");
  await options.getByLabel("Has attachment").check();
  await options.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("textbox", { name: "Search mail" })).toHaveValue("weekend has:attachment");
  await expect(page).toHaveURL(/view=inbox/);
  const found = page.getByRole("list", { name: "Mail" });
  await expect(found.getByRole("listitem")).toHaveCount(1);
  await expect(found.getByRole("link", { name: /Photos from the weekend/ })).toBeVisible();
  await page.getByRole("checkbox", { name: "Pick all" }).check();
  const picked = page.getByRole("region", { name: "Picked mail" });
  await expect(picked.getByText("The 1 mail in this search is picked.")).toBeVisible();
  await picked.getByRole("button", { name: "Offload", exact: true }).click();
  const offload = page.getByRole("dialog", { name: "Offload mail" });
  await offload.getByRole("button", { name: "Preview" }).click();
  await offload.getByLabel("To start, type 1").fill("1");
  await offload.getByRole("button", { name: "Start offload" }).click();
  await expect(page.getByText(/^Offloaded 1 of 1 mails/)).toBeVisible({ timeout: 60_000 });
  expect(fakeGmailTrash() ?? ["Photos from the weekend"]).toContain("Photos from the weekend");

  // 6. It's in Offloaded now and gone from the Inbox; restore it from Offloaded.
  await expect((await rows(page, "inbox")).getByRole("link", { name: /Photos from the weekend/ })).toHaveCount(0);
  const offloaded = await rows(page, "offloaded");
  await offloaded.getByRole("link", { name: /Photos from the weekend/ }).click();
  await page.getByRole("region", { name: "Open mail" }).getByRole("button", { name: "Restore to Gmail" }).click();
  await expect(page.getByText("Putting 1 mail back in Gmail. Sync activity shows when it's done.")).toBeVisible();
  await expect
    .poll(async () => (await rows(page, "inbox")).getByRole("link", { name: /Photos from the weekend/ }).count(), { timeout: 60_000 })
    .toBe(1);

  // 7. Delete a mail in Gmail itself: it shows in Deleted in Gmail, and Delete forever removes CleanSync's copy.
  if (fakeGmailDelete("Old newsletter #1")) {
    await page.goto("/mail?view=inbox");
    await page.getByRole("button", { name: "Check now" }).click();
    await expect
      .poll(async () => (await rows(page, "deleted")).getByRole("link", { name: /Old newsletter #1/ }).count(), { timeout: 60_000 })
      .toBe(1);
    const search = page.getByRole("textbox", { name: "Search mail" });
    await search.fill("newsletter");
    await search.press("Enter");
    await expect(page).toHaveURL(/view=deleted/);
    await page.getByRole("checkbox", { name: "Pick all" }).check();
    const bar = page.getByRole("region", { name: "Picked mail" });
    await expect(bar.getByText("The 1 mail in this search is picked.")).toBeVisible();
    await bar.getByRole("button", { name: "Delete forever…" }).click();
    const forever = page.getByRole("dialog", { name: "Delete forever" });
    await forever.getByLabel("Type 1 to delete forever").fill("1");
    await forever.getByRole("button", { name: "Delete forever" }).click();
    await expect(page.getByText("Deleted 1 mail forever.")).toBeVisible();
    await expect(page.getByText("No mail here.")).toBeVisible();
  }
```

Run it the way the rules project runs (see the `rules` project in `frontend/playwright.config.ts` and the CI job that sets `E2E_CONTAINER`), against a CleanSync built from this branch with the sample mailbox and the fake model. Expected: both rules tests and the smoke tests pass. The smoke test's offload (older than 1 year, 13 mails) and its restore of one mail run first and are unchanged.

- [ ] **Step 6: The docs**

**`docs/mail-backup.md`:**
- "What is kept, and where": replace the Local archive bullet with "Mail you offload (one mail, a pick, a search, or by a rule) goes to Gmail's trash, and CleanSync keeps its copy in **Offloaded**. From there you can restore it to Gmail, or **Delete forever** CleanSync's copy."
- "Reading": replace the first sentence with: "The Mail panel shows Gmail first: Inbox, Starred, Important, Sent, **Archived** (mail in Gmail that is in neither the Inbox nor Sent) and All mail, then your labels. These show only mail that is in Gmail. Below the line marked CleanSync are Suggested, Rules, **Offloaded** (every mail CleanSync removed from Gmail, with a **Batches** tab for offloads started from a filter or a pick) and **Deleted in Gmail**." Add `in:archived` to the list of search words. Add: "**Search options** (the button at the end of the search box) builds the search from a form: From, To, Has the words, Label, Size, Date, Has attachment, Unread, Starred. It reads the search box back when it opens, and keeps anything it doesn't know in Has the words. A search runs in the view you're in."
- New section "Picking mail", before "Offloading mail": "Tick mails in the list, or tick **Pick all** at the top to pick every mail of the view and search, not just the rows on screen. In Gmail's views you can then **Offload** them or **Make a rule…**; in Offloaded and Deleted in Gmail, **Restore to Gmail** or **Delete forever**."
- "Offloading mail": add "or from Pick all" after "from mails you picked". Add the sentence: "One mail open in the reader has its own **Offload**, which checks the copy and moves that mail to Gmail's trash at once."
- New section "Delete forever", after "Putting mail back": "In Offloaded and Deleted in Gmail, **Delete forever** removes CleanSync's own copy: the `.eml` file, its search entry and its rule history. The mail is already gone from Gmail, so afterwards it exists nowhere. You type the number of mails to confirm. Activity keeps a short record (sender, subject and date of up to 500 of them, and how many more). It refuses while a restore is running for that mailbox."
- "Limits to know about": replace the last bullet's second sentence with "The only way a copy is removed is **Delete forever** in Offloaded or Deleted in Gmail, which you confirm by typing the count."

**`docs/mail-rules.md`:**
- The intro: "or delete it (to Gmail's trash)" becomes "or offload it (out of Gmail, with CleanSync's copy kept)".
- §2 "What it does": "Archive, Label (the Gmail label is made if it doesn't exist), Mark read, Star, or Offload. Offload stands alone. **Delete forever** is listed but can't be picked yet: it needs full Gmail access, which comes later in this release. A rules file from an older version that says `delete` is read as Offload."
- §5: retitle it "Offload and Delete forever" and rewrite it: "**Offload** (a rule action, or Offload on one mail in the reader) first checks CleanSync's copy of the mail, the same way an offload does. Then it moves the mail to Gmail's trash and marks it Offloaded. Gmail empties its trash on its own after 30 days, and CleanSync can't empty it sooner (see [mail backup](mail-backup.md)). The mail shows in **Offloaded**, beside mail offloaded by a filter or a pick; it isn't part of any offload batch. Undo on the rule's chip takes it out of Gmail's trash again; **Restore to Gmail** works too, and imports it again if Gmail no longer has it. **Delete forever** in Offloaded removes CleanSync's own copy for good; see [mail backup](mail-backup.md#delete-forever). If you take an offloaded mail out of Gmail's trash yourself, CleanSync sees it on its next check and moves it back to your normal mail."
- Fix the anchor `#5-delete-and-the-local-archive` in `docs/mail-backup.md` to `#5-offload-and-delete-forever`.
- "Limits to know about": "The Delete-forever record" stays as it is.

**`docs/limitations.md`**, Mail section: replace "Delete moves mail to Gmail's trash and keeps CleanSync's copy in the Local archive." with "Offload moves mail to Gmail's trash and keeps CleanSync's copy in Offloaded. Delete forever works only on mail already out of Gmail (Offloaded, Deleted in Gmail) until full Gmail access arrives later in this release." In the Mail rules section, add: "Pick all picks every mail of the view and search; Make a rule… uses at most 10 of them."

Then: `grep -rn "Local archive\|local archive" docs/*.md`. Expected: nothing, apart from historical CHANGELOG lines (leave those).

- [ ] **Step 7: Full checks**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && cd ../frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/devtools/fake_gmail.py backend/app/services/mail/adapters.py backend/tests/test_fake_gmail_cli.py frontend/e2e/helpers.ts frontend/e2e/rules.spec.ts docs/mail-backup.md docs/mail-rules.md docs/limitations.md
git commit -m "test(e2e): offload a search with Pick all, restore it, delete forever in Deleted in Gmail; docs for the mail panel

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
