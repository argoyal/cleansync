# Running Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rules are run by hand from a rule card's **Run** (that rule over the Inbox) or from **Run rules** on a search or a pick (the ticked rules). The owner approves from a list of the matched mails, with everything picked at first, and types a count only for Offload and Delete forever. "Run rules on…" goes.

**Architecture:**
- A run of rules (`rule_jobs`) now keeps its own list of rules (`rule_ids`), plus the `view` and `q` it ran on (migration 0012). The sorting worker decides a run's mail with only those rules and only their remembered senders. `[]` (runs from before the upgrade) still means every enabled rule.
- `RuleJobs.start_preview(account_id, *, rule_ids, scope: RunScope)` takes the Inbox, a search, a pick-all `{view, label, q}` or a pick by hand, and refuses a start with neither a search nor a pick.
- `GET /api/rule-jobs/{id}/matches` pages the planned decisions. `POST /api/rule-jobs/{id}/run` takes `left_out` (or `only`) and a `confirm` that is needed only for Offload and Delete forever. Left-out decisions become `rejected` with "You left it out."
- The frontend's `RunRulesDialog` shows deciding, then the match list (round 1's Pick all checkbox, rule filter, Show more), then the result. A new `RulePickerDialog` comes first for a search or a pick. The rule card gets **Run**.

**Tech Stack:** FastAPI, SQLAlchemy 2 + Alembic on SQLite, pytest (`asyncio_mode = "auto"`), React 18 + TanStack Query + Tailwind, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-13-running-rules-design.md` (approved; round 2 of 3 of 0.5.0).

**Starts after round 1.** This plan runs on branch `feat/mail-ux` after `docs/superpowers/plans/2026-10-13-mail-panel-and-search.md` has been carried out. Every file and name below is as round 1 leaves it. The round-1 names this plan uses are listed in each task's **Consumes**. Round 3 (`2026-10-13-bulk-offload-design.md`) comes after this one.

## Global Constraints

- **Migration:** `0012`, `down_revision = "0011"`. `rule_jobs.rule_ids` is a JSON list, not null, and `[]` means "every enabled rule" (rows from before the upgrade). `rule_jobs.view` and `rule_jobs.q` are not null, default `""`.
- **Where Run rules shows:** only in Gmail's views (Inbox, Starred, Important, Sent, Archived, All mail, label views), and only when the search box isn't empty or mail is picked. Never in Suggested, Offloaded or Deleted in Gmail, never on the Rules page.
- **Run on a rule card** covers mail in Gmail with the `INBOX` label, with that one rule alone. It is disabled with "Turn the rule on to run it." when the rule is off, and with "Set up a model in Settings → Models." when the rule needs the model (any rule not `fixed_only`) and no Sorting model is set.
- **Several rules:** each mail gets at most one rule, the first ticked rule that fits, in the rules' order.
- **The match list:** 50 a page with **Show more**; **Pick all** starts ticked; the server stores the mails left out.
- **The typed count** is asked only when picked changes offload or delete forever (`HEAVY_KINDS = ("offload", "delete_forever")`), and it is the number of those picked mails.
- **Kept as today:** one run at a time per mailbox; at most 20,000 mails ("That's more than 20,000 mails. Please narrow the search."); mail being decided by sorting and mail moved back by the owner are skipped; a paused model leaves a run deciding; Sort new inbox mail, Suggested, Undo and remembered senders are unchanged.
- **Copy, exactly:** "Run", "Run rules", "Turn the rule on to run it.", "Set up a model in Settings → Models.", "Search first, or pick some mail.", "You left it out.", "No mail fits this rule.", "Run on: 1,284 mails matching from:shop.example.com", "Deciding… 340 of 1,284", "Marketing would archive 412 mails. 872 didn't fit.", "Marketing · archive · 412", "Archive 412 mails", "Apply 442 changes", "Type 120 to offload these mails".
- **Removed:** "Run rules on…" everywhere (Rules page footer, mail header), `runRulesStart` in `lib/mail.ts`, and the typed total for every change.
- **Not in this round:** the Delete forever action working (round 3); running rules in Offloaded or Deleted in Gmail; scheduled runs; any change to Sort new inbox mail.
- **Copy style:** plain everyday words, matching the existing UI text.
- **Tests must pass:** backend `cd backend && uv run pytest -q`, lint `uv run ruff check . && uv run ruff format --check .`. Some Python lines in this plan are longer than ruff's 100 columns: run `uv run ruff format` on the files you changed before the format check. Frontend `cd frontend && npm test -- --run && npm run lint && npm run typecheck`.
- **Commits:** every commit message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Only stage the files the task names.

## Plan decisions on the spec

These settle details the spec left open. Reviewers judge against them.

1. **Order.** The backend comes first (Tasks 1–3) and keeps the old frontend working until Task 4 swaps the dialog. Task 4 also takes "Run rules on…" out, so between Task 4 and Tasks 5–6 the only way to run a rule is Make a rule…'s "Run it on older mail…". Every commit builds and passes.
2. **Picks by hand are threads.** Round 1 picks threads (`thread_ids`), not mails. So `POST /api/rule-jobs` also takes `thread_ids` with `view` and `label`. It means the mails of those threads that are in that view: a thread picked in the Inbox runs on its Inbox mail, not on the owner's reply in Sent. `message_ids` stays in the API as the spec says, but no screen sends it. The picker says "Run on: 3 picked threads".
3. **A search and a pick-all are the same run.** `{view, label, q}` needs a non-empty `q`; with `pick_all: true` the search may be empty (a whole Inbox, Archived or label view). A run's view must be a Gmail view other than Suggested, or it is refused with "Rules run on mail in Gmail's views only." Unlike Offload, a pick-all of All mail with no search is allowed: the 20,000 limit covers it.
4. **The server checks the rules:** each id must be this mailbox's ("That rule no longer exists.") and turned on ("Turn the rule on to run it."). The Inbox run needs exactly one ("Run on the Inbox takes one rule."), and an empty list is refused ("Pick at least one rule."). The server doesn't check the model: a rule that needs one when none is set decides "no rule", as sorting does today. The screens grey such rules out.
5. **The Run button is on every card,** turned on or not, and disabled with its reason when the rule is off (the spec says both "every enabled rule's card" and "disabled when the rule is turned off"). The reason is shown next to the button and is the button's description for screen readers.
6. **The rule picker lists only rules that are on,** in their order. A rule that needs the model when none is set is listed but can't be ticked, with the card's reason under it.
7. **Run rules in the mail view:** above the list when there is a search and nothing is picked; on the picked-mail bar when something is picked (by hand or Pick all). Never both. With a pick-all it says "Run on: 1,284 mails matching older_than:1y", or "Run on: 1,204 mails in Inbox" with no search.
8. **`only` in the approve body.** Unticking Pick all leaves nothing picked; ticking rows after that sends `only: [ids]`, and the server leaves out every other match. The browser doesn't hold every page of the list, so it can't name all the others itself. `left_out` stays the normal case, as the spec says.
9. **Pick all covers the whole match list,** every rule. The rule filter ("Show") only changes which rows are shown; picks are kept when it changes.
10. **The approve button:** when the picked mails all belong to one rule that has one action, it names the action and the count: "Archive 412 mails", "Star 3 mails", "Mark 5 mails read", "Add the label Receipts to 30 mails", "Offload 120 mails", "Delete 2 mails forever". Otherwise, including one rule with two actions such as the Marketing starter (label and archive), it says "Apply 442 changes" (one change per mail).
11. **The header:** one rule with one action says "Marketing would archive 412 mails. 872 didn't fit."; one rule with several actions says "Marketing would change 2 mails: Label Marketing, Archive. 0 didn't fit."; several rules get one line each ("Marketing · archive · 412", "Receipts · label Receipts · 30") and then "872 didn't fit." A line "On: Inbox · from:shop" above says what it ran on (the job's `filter_text`).
12. **The typed count** is the number of picked mails whose rule offloads or deletes forever. The field says "Type 120 to offload these mails", "Type 1 to delete these mails forever", or "Type 3 to offload or delete forever these mails". The server refuses a wrong or missing count with "The number you typed doesn't match the 120 mails to offload." Round 3 needs no change here.
13. **Approving with nothing picked:** the button is disabled. The server still accepts it, and the run ends at once with every match left out.
14. **The match list order** is newest decision first (`id` descending), paged by `before_id`, 50 a page; the server caps `limit` at 200. It answers a plain list. The screen offers **Show more** while the last page came back full.
15. **`JobPreview` gains** `rule_ids`, `view`, `q`, `filter_text` (Task 2), `left_out` and `skip_reasons` (`[{reason, count}]`, Task 3). Each `per_rule` entry gains `rule_id`. The label isn't a column of its own: `filter_text` names it, through round 1's `describe_pick` ("Inbox", "Inbox · from:shop", "Receipts", "3 picked mails").
16. **The end line** is "Done. Applied 1, skipped 2, failed 0, left out 3." (or "Stopped. …"), with a list of the skip reasons and their counts. It replaces "N mails were left as they were because they changed since the preview."
17. **Nothing matched:** "No mail fits this rule." for one rule, "No mail fits these rules." for several, with Close.
18. **Make a rule… → Run it on older mail…** runs only the saved rule: on its one sender's mail (`{view: "all", q: "from:x"}`), else on the list on screen as a pick-all (a Gmail view that isn't All mail with no search or label), else on the Inbox.
19. **Sorting's give-up message** "Run rules on it again to retry." becomes "Run its rule again to retry."
20. **The sample mailbox gains two marketing mails** ("Big sale: 30% off shoes", "Spring offers just for you", from `deals@shop.example.com`, with a `List-Unsubscribe` header, in the Inbox, 15 and 25 days old). No sample mail fits the Marketing starter today, and the browser test needs some. The sample's counts become 26 kept of 28, and the smoke test's counts follow.
21. **The browser test runs the search first, then the card.** It runs Marketing on `from:deals` with one mail unticked (that mail stays in the Inbox), then Run on the Marketing card, which finds the mail left out before and archives it. Both mails end in Archived. The test runs after the first rules test, which points Sorting at the pretend model, and doesn't call `setUpMail` (round 1's Delete forever step can change the kept count).
22. **`CHANGELOG.md` is not touched,** as in round 1. The release notes are written when 0.5.0 is released.
23. **An empty Inbox** gives "No mail in the Inbox to run this rule on."; an empty search or pick keeps today's "No mail in Gmail matches this search."

## Review Focus

1. **A picked thread whose reply is in Sent, run from the Inbox.** Only the thread's Inbox mail is decided, never the owner's own reply. Owned by Task 2 (`test_picked_threads_cover_only_their_mails_in_the_view`).
2. **A sender remembered for two rules when only the lower rule runs.** The run must use the lower rule's memory, not drop the sender because the higher rule won the tie. Owned by Task 1 (`test_remembered_senders_count_only_for_the_runs_rules`).
3. **Pick all unticked, then a few rows ticked while more pages exist.** Only those rows may change; the unloaded pages must be left out. Owned by Task 3 (`test_only_runs_just_the_ticked_matches`) and Task 4 ("unticking Pick all leaves nothing picked; ticked rows are sent as the only ones").
4. **Some offload rows unticked.** The typed count must follow the picks, not the rule's total, and the server must check the same number. Owned by Task 3 (`test_the_typed_count_is_asked_only_for_picked_offloads`) and Task 4 ("asks for the typed count only for picked offloads").
5. **A run reopened from the Rules page after deciding finished.** It must show the match list with every mail picked and start nothing new; Show more must page on. Owned by Task 4 ("reopens a run with its match list, and Show more loads the next page").

---

### Task 1: A run decides with only its own rules (migration 0012)

**Files:**
- Create: `backend/app/database/migrations/versions/0012_rule_job_rules.py`
- Modify: `backend/app/models/rules.py` (`RuleJob.rule_ids`, `view`, `q`)
- Modify: `backend/app/services/rules/store.py` (`rule_views` takes `rule_ids`)
- Modify: `backend/app/services/rules/decisions.py` (`remembered_senders` takes `rule_ids`)
- Modify: `backend/app/services/rules/worker.py` (`job_rules`, `_decide_one`, `GAVE_UP`)
- Create: `backend/tests/test_migration_0012.py`, `backend/tests/test_rules_running.py`
- Modify: `backend/tests/test_database.py`, `backend/tests/test_migration_0011.py`

**Interfaces:**
- Consumes: round 1's migration `0011` (`backend/app/database/migrations/versions/0011_offloaded_and_offload_action.py`) and its test `backend/tests/test_migration_0011.py`.
- Produces:
  - `RuleJob.rule_ids: list[int]` (default `[]`), `RuleJob.view: str` (default `""`), `RuleJob.q: str` (default `""`)
  - `store.rule_views(db, account_id, enabled_only=True, rule_ids: list[int] | None = None) -> list[RuleView]`
  - `decisions.remembered_senders(db, account_id, rule_ids: list[int] | None = None) -> dict[str, int]`
  - `worker.job_rules(db: Session, job_id: int | None) -> list[int] | None` (None: every enabled rule)
  - Test helpers in `tests/test_rules_running.py`: `ARCHIVE`, `ByName`, `rule(gbox, name, actions=ARCHIVE, **kw) -> int`, `worker_for(gbox, chooser)`, `decide_all(gbox, chooser)`, `job_with(gbox, rule_ids, *rows) -> int`, `verdicts(gbox, jid) -> dict[str, tuple[str, str]]`, `remember(gbox, rule_id, sender)`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_migration_0012.py
"""Migration 0012: a run of rules keeps its own rules, view and search (running rules spec §8)."""

from sqlalchemy import text

from app.database.engine import make_engine
from app.database.migrate import run_migrations
from tests.test_mail_speed_schema import _downgrade

STAMP = "2026-01-02 03:04:05.000000"


def read(f, sql):
    eng = make_engine(f)
    try:
        with eng.begin() as c:
            return c.execute(text(sql)).all()
    finally:
        eng.dispose()


def test_old_runs_mean_every_rule_and_the_way_back_works(tmp_path):
    f = tmp_path / "v.db"
    run_migrations(f, "0011")
    eng = make_engine(f)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO accounts (id, email, remote_name, status, connected_at) "
                "VALUES (1, 'a@x.com', 'a', 'CONNECTED', :t)"
            ),
            {"t": STAMP},
        )
        c.execute(
            text(
                "INSERT INTO rule_jobs (id, account_id, filter_text, state, total, decided, "
                "applied, skipped, failed, created_at) "
                "VALUES (1, 1, 'older_than:1y', 'DONE', 3, 3, 2, 1, 0, :t)"
            ),
            {"t": STAMP},
        )
    eng.dispose()
    run_migrations(f)
    assert read(f, "SELECT version_num FROM alembic_version") == [("0012",)]
    assert read(f, "SELECT id, rule_ids, view, q, filter_text FROM rule_jobs") == [
        (1, "[]", "", "", "older_than:1y")
    ]
    _downgrade(f, "0011")
    assert read(f, "SELECT id, filter_text, applied FROM rule_jobs") == [(1, "older_than:1y", 2)]
    columns = {r[1] for r in read(f, "PRAGMA table_info(rule_jobs)")}
    assert not {"rule_ids", "view", "q"} & columns
    (sql,) = read(f, "SELECT sql FROM sqlite_master WHERE name = 'rule_jobs'")[0]
    assert "AUTOINCREMENT" in sql
```

```python
# backend/tests/test_rules_running.py
"""Running rules (running rules spec §3-§5, §8): a run decides with its own rules only, covers
the Inbox, a search, a pick-all or a pick, and is approved from its match list."""

from sqlalchemy import select

from app.models import MailMessage, RuleDecision, RuleJob, RuleSender, RuleTodo
from app.services.rules.decisions import remembered_senders
from app.services.rules.match import Choice
from app.services.rules.store import RuleIn, create_rule
from app.services.rules.worker import SortingWorker

ARCHIVE = [{"kind": "archive"}]


class ByName:
    """A pretend model: the first rule it is offered whose name is in the subject. It keeps
    the names it was offered, one list per mail."""

    def __init__(self):
        self.offered: list[list[str]] = []

    async def choose(self, rules, mail, hints):
        self.offered.append([r.name for r in rules])
        for r in rules:
            if r.name.lower() in mail.subject.lower():
                return Choice(r.id, f"subject mentions {r.name}")
        return Choice(None, "nothing fits")


def rule(gbox, name, actions=ARCHIVE, **kw) -> int:
    with gbox.sf() as db:
        data = RuleIn(name=name, description=f"{name} mail", actions=actions, **kw)
        return create_rule(db, gbox.acc.id, data, "a").id


def worker_for(gbox, chooser) -> SortingWorker:
    return SortingWorker(
        session_factory=gbox.sf, layout=gbox.layout, chooser=lambda: chooser, applier=gbox.applier
    )


async def decide_all(gbox, chooser) -> None:
    w = worker_for(gbox, chooser)
    while await w.run_once(gbox.acc.id):
        pass


def job_with(gbox, rule_ids, *rows) -> int:
    """A run as it is queued. A run from before migration 0012 has rule_ids []."""
    with gbox.sf() as db:
        job = RuleJob(account_id=gbox.acc.id, total=len(rows), rule_ids=rule_ids)
        db.add(job)
        db.flush()
        for r in rows:
            db.add(RuleTodo(account_id=gbox.acc.id, message_id=r.id, job_id=job.id))
        db.commit()
        return job.id


def verdicts(gbox, jid) -> dict[str, tuple[str, str]]:
    """Subject -> (the rule's name, how it was decided), for one run."""
    with gbox.sf() as db:
        rows = db.execute(
            select(MailMessage.subject, RuleDecision.rule_name, RuleDecision.how)
            .join(MailMessage, MailMessage.id == RuleDecision.message_id)
            .where(RuleDecision.job_id == jid)
        ).all()
    return {s: (n, str(h)) for s, n, h in rows}


def remember(gbox, rule_id, sender) -> None:
    with gbox.sf() as db:
        db.add(RuleSender(rule_id=rule_id, account_id=gbox.acc.id, sender=sender, approvals=3))
        db.commit()


async def test_a_run_decides_with_only_its_rules(gbox):
    rule(gbox, "Shops")
    news = rule(gbox, "News")
    both = gbox.add("Shops and News")
    shops_only = gbox.add("Shops sale")
    news_only = gbox.add("News today")
    chooser = ByName()
    jid = job_with(gbox, [news], both, shops_only, news_only)
    await decide_all(gbox, chooser)
    assert verdicts(gbox, jid) == {
        "Shops and News": ("News", "ai"),  # Shops comes first, but isn't part of this run
        "Shops sale": ("", "ai"),  # only Shops fits: no rule
        "News today": ("News", "ai"),
    }
    assert chooser.offered == [["News"], ["News"], ["News"]]


async def test_a_run_from_before_the_upgrade_uses_every_enabled_rule(gbox):
    rule(gbox, "Shops")
    rule(gbox, "News")
    rule(gbox, "Off", enabled=False)
    m = gbox.add("Shops and News and Off")
    chooser = ByName()
    jid = job_with(gbox, [], m)
    await decide_all(gbox, chooser)
    assert verdicts(gbox, jid) == {"Shops and News and Off": ("Shops", "ai")}
    assert chooser.offered == [["Shops", "News"]]


async def test_remembered_senders_count_only_for_the_runs_rules(gbox):
    shops, news = rule(gbox, "Shops"), rule(gbox, "News")
    remember(gbox, shops, "a@example.com")
    remember(gbox, shops, "both@example.com")
    remember(gbox, news, "both@example.com")  # Shops, higher up, wins this tie in sorting
    from_a = gbox.add("News from a", sender="a@example.com")
    from_both = gbox.add("Hello", sender="both@example.com")
    chooser = ByName()
    jid = job_with(gbox, [news], from_a, from_both)
    await decide_all(gbox, chooser)
    assert verdicts(gbox, jid) == {
        "News from a": ("News", "ai"),
        "Hello": ("News", "remembered"),
    }
    assert chooser.offered == [["News"]]
    with gbox.sf() as db:
        assert remembered_senders(db, gbox.acc.id) == {
            "a@example.com": shops,
            "both@example.com": shops,
        }
        assert remembered_senders(db, gbox.acc.id, rule_ids=[news]) == {"both@example.com": news}
```

In `backend/tests/test_database.py`, `test_a_v0_2_1_database_upgrades_to_ai_rules`: the heads become `["0012"]`, add `assert script.get_revision("0012").down_revision == "0011"` above the `"0011"` line, and the version after `run_migrations(f)` becomes `"0012"`.

In `backend/tests/test_migration_0011.py`, `test_local_archive_mail_becomes_offloaded`: `[("0011",)]` becomes `[("0012",)]` (the database now upgrades to the newest revision).

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_migration_0012.py tests/test_rules_running.py tests/test_database.py tests/test_migration_0011.py -q`
Expected: FAIL. There is no revision 0012, `RuleJob` has no `rule_ids`, and `remembered_senders` takes no `rule_ids`.

- [ ] **Step 3: Write the migration**

```python
# backend/app/database/migrations/versions/0012_rule_job_rules.py
"""A run of rules keeps its own rules, view and search

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Runs from before this keep [] in rule_ids, which means every enabled rule
    # (running rules spec §8).
    with op.batch_alter_table("rule_jobs", table_kwargs={"sqlite_autoincrement": True}) as b:
        b.add_column(sa.Column("rule_ids", sa.JSON(), nullable=False, server_default="[]"))
        b.add_column(sa.Column("view", sa.String(32), nullable=False, server_default=""))
        b.add_column(sa.Column("q", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("rule_jobs", table_kwargs={"sqlite_autoincrement": True}) as b:
        for col in ("q", "view", "rule_ids"):
            b.drop_column(col)
```

- [ ] **Step 4: The model**

In `backend/app/models/rules.py`, in `class RuleJob`, add after `filter_text`:

```python
    # The rules this run decides with (running rules spec §8). [] for runs from before
    # migration 0012: every enabled rule. `view` and `q` say what it ran on.
    rule_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    view: Mapped[str] = mapped_column(String(32), default="")
    q: Mapped[str] = mapped_column(Text, default="")
```

- [ ] **Step 5: Rules and remembered senders limited to a list**

In `backend/app/services/rules/store.py`, replace `rule_views` with:

```python
def rule_views(
    db: Session, account_id: int, enabled_only: bool = True, rule_ids: list[int] | None = None
) -> list[RuleView]:
    """The mailbox's rules in their order. `rule_ids` keeps only those (a run's own rules)."""
    q = select(Rule).where(Rule.account_id == account_id)
    if enabled_only:
        q = q.where(Rule.enabled.is_(True))
    if rule_ids is not None:
        q = q.where(Rule.id.in_(rule_ids))
    return [view_of(r) for r in db.scalars(q.order_by(Rule.position, Rule.id))]
```

In `backend/app/services/rules/decisions.py`, replace `remembered_senders` with:

```python
def remembered_senders(
    db: Session, account_id: int, rule_ids: list[int] | None = None
) -> dict[str, int]:
    """Sender -> rule, for senders approved into a rule three times. `rule_ids` keeps only
    those rules' senders (a run's own rules), so a tie a run's rule loses elsewhere still
    counts for it here."""
    q = (
        select(RuleSender.sender, RuleSender.rule_id)
        .join(Rule, Rule.id == RuleSender.rule_id)
        .where(
            RuleSender.account_id == account_id,
            RuleSender.approvals >= REMEMBER_AFTER,
            Rule.enabled.is_(True),
        )
    )
    if rule_ids is not None:
        q = q.where(RuleSender.rule_id.in_(rule_ids))
    out: dict[str, int] = {}
    for sender, rule_id in db.execute(q.order_by(Rule.position)).all():
        out.setdefault(sender, rule_id)  # the rule higher in the list wins a tie
    return out
```

- [ ] **Step 6: The worker decides a run with its rules**

In `backend/app/services/rules/worker.py`:
- `GAVE_UP`'s second sentence `"Run rules on it again to retry."` becomes `"Run its rule again to retry."`.
- Add after `wake_waiting`:

```python
def job_rules(db: Session, job_id: int | None) -> list[int] | None:
    """The rules a run of rules decides with (running rules spec §8). None means every enabled
    rule: for new inbox mail, and for runs from before migration 0012 (rule_ids [])."""
    if job_id is None:
        return None
    ids = db.scalar(select(RuleJob.rule_ids).where(RuleJob.id == job_id))
    return [int(i) for i in ids] if ids else None
```

- In `_decide_one`, replace

```python
        with self.sf() as db:
            rules = rule_views(db, account_id)
            remembered = remembered_senders(db, account_id)
```

with

```python
        with self.sf() as db:
            only = job_rules(db, job_id)
            rules = rule_views(db, account_id, rule_ids=only)
            remembered = remembered_senders(db, account_id, rule_ids=only)
```

- [ ] **Step 7: Run the tests to check they pass**

Run: `cd backend && uv run pytest tests/test_migration_0012.py tests/test_rules_running.py tests/test_database.py tests/test_migration_0011.py tests/test_rules_worker.py tests/test_rules_jobs.py tests/test_rules_decisions.py -q`
Expected: PASS. (If the downgrade's `AUTOINCREMENT` check fails, the batch copy lost the table option: keep `table_kwargs={"sqlite_autoincrement": True}` on both batches, as written.)

- [ ] **Step 8: Run the full backend suite and lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/database/migrations/versions/0012_rule_job_rules.py backend/app/models/rules.py backend/app/services/rules/store.py backend/app/services/rules/decisions.py backend/app/services/rules/worker.py backend/tests/test_migration_0012.py backend/tests/test_rules_running.py backend/tests/test_database.py backend/tests/test_migration_0011.py
git commit -m "feat(rules): a run of rules keeps its own rules and decides with only them

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Starting a run on the Inbox, a search, a pick-all or a pick

**Files:**
- Modify: `backend/app/services/rules/jobs.py` (`RunScope`, `start_preview`, `JobPreview` fields, `preview`)
- Modify: `backend/app/schemas/rules.py` (`JobStartBody`, `JobPreviewOut`)
- Modify: `backend/app/api/rules.py` (`start_job`)
- Modify: `backend/tests/test_rules_running.py`, `backend/tests/test_rules_jobs.py`, `backend/tests/test_rules_api.py`

**Interfaces:**
- Consumes:
  - Task 1's `RuleJob.rule_ids`, `view`, `q`, and the test helpers in `tests/test_rules_running.py`.
  - Round 1: `app.services.mail.search.GMAIL_VIEWS` and `mail_conditions(db, account_id, *, view="all", label=None, q="", now)`; `app.services.mail.offload_plan.Pick(q=None, message_ids=None, thread_ids=None, view="all", label=None)` and `describe_pick(db, account_id, pick, n) -> str`.
- Produces:
  - `jobs.RunScope(inbox=False, view="all", label=None, q="", pick_all=False, message_ids=None, thread_ids=None)` (dataclass)
  - `RuleJobs.start_preview(account_id: int, *, rule_ids: list[int], scope: RunScope) -> int`
  - `jobs.RUN_VIEWS` (`GMAIL_VIEWS` without `"suggested"`), and the messages `NO_RULES_MSG`, `ONE_RULE_MSG`, `RULE_GONE_MSG`, `RULE_OFF_MSG`, `NO_SCOPE_MSG`, `VIEW_MSG`, `NO_MAIL_MSG`, `EMPTY_INBOX_MSG`
  - `JobPreview` gains `rule_ids: list[int]`, `view: str`, `q: str`, `filter_text: str`; each `per_rule` dict gains `"rule_id"`.
  - `POST /api/rule-jobs` body: `{account_id, rule_ids, inbox?, view?, label?, q?, pick_all?, message_ids?, thread_ids?}`.
  - Test helpers in `tests/test_rules_running.py`: `jobs_for(gbox, chooser=None) -> (SortingWorker, RuleJobs)`, `covered(gbox, jid) -> list[int]`. In `tests/test_rules_jobs.py`: `INBOX`, `news_rule(gbox) -> int`, `on_inbox(gbox, jobs) -> int`. In `tests/test_rules_api.py`: `_rule_id(auth_client, acc) -> int`, `_on_inbox(acc, rid) -> dict`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_rules_running.py`, add `import re` and `import pytest` at the top, add `from app.services.rules.jobs import RuleJobProblem, RuleJobs, RunScope` to the imports, and add at the end:

```python
def jobs_for(gbox, chooser=None) -> tuple[SortingWorker, RuleJobs]:
    w = worker_for(gbox, chooser or ByName())
    return w, RuleJobs(gbox.sf, gbox.applier, w)


def covered(gbox, jid) -> list[int]:
    """The mails a run queued for deciding."""
    with gbox.sf() as db:
        return sorted(db.scalars(select(RuleTodo.message_id).where(RuleTodo.job_id == jid)))


async def test_the_inbox_run_covers_only_inbox_mail(gbox):
    news = rule(gbox, "News")
    _, jobs = jobs_for(gbox)
    with pytest.raises(RuleJobProblem, match=r"^No mail in the Inbox to run this rule on\.$"):
        jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=RunScope(inbox=True))
    inbox = gbox.add("In the inbox")
    gbox.add("Archived", labels=("Label_1",))
    gbox.add("Sent", labels=("SENT",))
    jid = jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=RunScope(inbox=True))
    assert covered(gbox, jid) == [inbox.id]
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.rule_ids, p.view, p.q, p.filter_text) == ([news], "inbox", "", "Inbox")


async def test_a_search_or_a_pick_all_runs_in_its_view_and_label(gbox):
    news = rule(gbox, "News")
    a = gbox.add("News one")
    b = gbox.add("News two", labels=("Label_1",))
    other = gbox.add("Other")
    _, jobs = jobs_for(gbox)

    def run_on(**scope) -> list[int]:
        jid = jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=RunScope(**scope))
        return covered(gbox, jid)

    assert run_on(view="all", q="news") == sorted([a.id, b.id])
    assert run_on(view="inbox", q="news") == [a.id]
    assert run_on(view="archived", q="news") == [b.id]
    assert run_on(view="all", label="Label_1", q="", pick_all=True) == [b.id]
    assert run_on(view="inbox", q="", pick_all=True) == sorted([a.id, other.id])
    jid = jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=RunScope(view="inbox", q=" news "))
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.view, p.q, p.filter_text) == ("inbox", "news", "Inbox · news")


async def test_picked_threads_cover_only_their_mails_in_the_view(gbox):
    news = rule(gbox, "News")
    first = gbox.add("News")
    reply = gbox.add("Re: News", labels=("SENT",))
    with gbox.sf() as db:
        db.get(MailMessage, reply.id).thread_id = first.thread_id
        db.commit()
    _, jobs = jobs_for(gbox)

    def run_on(view) -> list[int]:
        scope = RunScope(view=view, thread_ids=[first.thread_id])
        return covered(gbox, jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=scope))

    assert run_on("inbox") == [first.id]  # the owner's reply in Sent isn't part of it
    assert run_on("all") == sorted([first.id, reply.id])
    jid = jobs.start_preview(gbox.acc.id, rule_ids=[news], scope=RunScope(message_ids=[reply.id]))
    assert covered(gbox, jid) == [reply.id]
    assert jobs.preview(gbox.acc.id, jid).filter_text == "1 picked mail"


async def test_a_start_needs_rules_and_a_search_or_a_pick(gbox, make_account):
    news, other = rule(gbox, "News"), rule(gbox, "Other")
    off = rule(gbox, "Off", enabled=False)
    bob = make_account("bob@example.com")
    with gbox.sf() as db:
        bobs = create_rule(db, bob.id, RuleIn(name="Bob's", description="x", actions=ARCHIVE), "a")
        bobs_id = bobs.id
    gbox.add("News")
    _, jobs = jobs_for(gbox)
    cases = [
        ([], RunScope(inbox=True), "Pick at least one rule."),
        ([news, other], RunScope(inbox=True), "Run on the Inbox takes one rule."),
        ([news], RunScope(view="inbox"), "Search first, or pick some mail."),
        ([news], RunScope(view="all", q="   "), "Search first, or pick some mail."),
        ([news], RunScope(view="offloaded", q="news"), "Rules run on mail in Gmail's views only."),
        ([news], RunScope(view="suggested", pick_all=True), "Rules run on mail in Gmail's views only."),
        ([off], RunScope(inbox=True), "Turn the rule on to run it."),
        ([news + 1000], RunScope(inbox=True), "That rule no longer exists."),
        ([bobs_id], RunScope(inbox=True), "That rule no longer exists."),
        ([news], RunScope(view="all", q="in:spam"), '"in:" only knows in:archived.'),
    ]
    for rule_ids, scope, msg in cases:
        with pytest.raises(RuleJobProblem, match=f"^{re.escape(msg)}$"):
            jobs.start_preview(gbox.acc.id, rule_ids=rule_ids, scope=scope)
    with gbox.sf() as db:
        assert db.scalars(select(RuleJob)).all() == []


async def test_each_mail_gets_the_first_ticked_rule_that_fits_in_the_rules_order(gbox):
    rule(gbox, "Alpha")
    beta, gamma = rule(gbox, "Beta"), rule(gbox, "Gamma")
    gbox.add("Gamma Beta Alpha")
    chooser = ByName()
    worker, jobs = jobs_for(gbox, chooser)
    scope = RunScope(view="all", q="gamma")
    jid = jobs.start_preview(gbox.acc.id, rule_ids=[gamma, beta], scope=scope)  # ticked in reverse
    while await worker.run_once(gbox.acc.id):
        pass
    assert verdicts(gbox, jid) == {"Gamma Beta Alpha": ("Beta", "ai")}
    assert chooser.offered == [["Beta", "Gamma"]]
    assert jobs.preview(gbox.acc.id, jid).rule_ids == [gamma, beta]
```

In `backend/tests/test_rules_api.py`, add after `RULE`:

```python
def _rule_id(auth_client, acc) -> int:
    return auth_client.post("/api/rules", json={"account_id": acc.id, **RULE}).json()["id"]


def _on_inbox(acc, rid) -> dict:
    """The body of a rule card's Run: one rule over the Inbox."""
    return {"account_id": acc.id, "rule_ids": [rid], "inbox": True}
```

and add this test after `test_a_previewed_job_runs_and_stops_from_the_api`:

```python
def test_starting_a_run_needs_rules_and_a_search_or_a_pick(auth_client, acc, db, layout):
    from app.devtools.fake_gmail import make_mail
    from tests.rules_helpers import keep

    keep(db, layout, acc, make_mail("Weekly digest"))
    rid = _rule_id(auth_client, acc)
    B = {"account_id": acc.id}
    r = auth_client.post("/api/rule-jobs", json={**B, "rule_ids": [rid], "view": "inbox"})
    assert r.status_code == 400 and r.json()["detail"] == "Search first, or pick some mail."
    r = auth_client.post("/api/rule-jobs", json={**B, "view": "inbox", "q": "digest"})
    assert r.status_code == 400 and r.json()["detail"] == "Pick at least one rule."
    body = {**B, "rule_ids": [rid], "view": "inbox", "q": "digest"}
    r = auth_client.post("/api/rule-jobs", json=body)
    assert r.status_code == 202
    p = auth_client.get(f"/api/rule-jobs/{r.json()['job_id']}", params=B).json()
    assert (p["rule_ids"], p["view"], p["q"], p["filter_text"], p["total"]) == (
        [rid],
        "inbox",
        "digest",
        "Inbox · digest",
        1,
    )
```

Then change the old starts in `backend/tests/test_rules_api.py` (every `{"q": "", "view": "inbox"}` body) to a rule card's Run:
- `test_rule_job_start_and_refusals`: add `rid = _rule_id(auth_client, acc)` as its first line after the imports; both `auth_client.post("/api/rule-jobs", json={"account_id": acc.id, "q": "", "view": "inbox"})` become `auth_client.post("/api/rule-jobs", json=_on_inbox(acc, rid))`.
- `test_a_previewed_job_runs_and_stops_from_the_api`: add `rid = _rule_id(auth_client, acc)` after the `keep(...)` line; the start's `json=` becomes `json=_on_inbox(acc, rid)`.
- `test_another_mailbox_cannot_touch_this_ones_objects`: the start's `json=` becomes `json=_on_inbox(acc, rid)`.
- `test_the_open_run_of_rules_can_be_found_again`: the start becomes `jid = auth_client.post("/api/rule-jobs", json=_on_inbox(acc, _rule_id(auth_client, acc))).json()["job_id"]`.
- `test_rule_job_preview_carries_the_pause`: the start's `json=` becomes `json=_on_inbox(acc, _rule_id(auth_client, acc))`.

In `backend/tests/test_rules_jobs.py`:
- The imports become `from app.models import MailMessage, Rule, RuleDecision, RuleJob, RuleTodo` and `from app.services.rules.jobs import RuleJobProblem, RuleJobs, RunScope`.
- Replace the `preview` helper with these helpers:

```python
INBOX = RunScope(inbox=True)


def news_rule(gbox) -> int:
    """The News rule's id, made the first time a test needs it."""
    with gbox.sf() as db:
        rid = db.scalar(
            select(Rule.id).where(Rule.account_id == gbox.acc.id, Rule.name == "News")
        )
        return rid if rid is not None else create_rule(db, gbox.acc.id, ARCHIVE, "a").id


def on_inbox(gbox, jobs) -> int:
    """A rule card's Run of News over the Inbox."""
    return jobs.start_preview(gbox.acc.id, rule_ids=[news_rule(gbox)], scope=INBOX)


async def preview(gbox, worker, jobs, scope=INBOX):
    jid = jobs.start_preview(gbox.acc.id, rule_ids=[news_rule(gbox)], scope=scope)
    while await worker.run_once(gbox.acc.id):
        pass
    return jid, jobs.preview(gbox.acc.id, jid)
```

- Then change the calls with:

```bash
cd backend && sed -i '' \
  -e 's/preview(gbox, worker, jobs, q="", view="inbox")/preview(gbox, worker, jobs)/' \
  -e 's/preview(gbox, worker, jobs, view="inbox")/preview(gbox, worker, jobs)/' \
  -e 's/jobs\.start_preview(gbox\.acc\.id, view="inbox")/on_inbox(gbox, jobs)/' \
  -e 's/jobs\.start_preview(gbox\.acc\.id, message_ids=\[old\.id\])/jobs.start_preview(gbox.acc.id, rule_ids=[news_rule(gbox)], scope=RunScope(message_ids=[old.id]))/' \
  tests/test_rules_jobs.py && grep -n 'view="inbox"\|message_ids=\[old' tests/test_rules_jobs.py
```

Expected: the grep prints only the new `RunScope(message_ids=[old.id])` line. Wrap that line for ruff (`uv run ruff format tests/test_rules_jobs.py` does it).

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_rules_running.py tests/test_rules_jobs.py tests/test_rules_api.py -q`
Expected: FAIL with `ImportError: cannot import name 'RunScope'`.

- [ ] **Step 3: The run's scope and its checks**

In `backend/app/services/rules/jobs.py`:
- Replace the module docstring with:

```python
"""Running rules by hand (running rules spec): a run decides the mail it covers with its own
rules, using only CleanSync's copy (nothing changes in Gmail). Then the owner approves from the
match list and the picked changes are made. Approving counts as the approval, whatever each
rule's mode is."""
```

- The imports become:

```python
import asyncio
import contextlib
import logging
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import MailMessage, Rule, RuleDecision, RuleJob, RuleTodo
from app.models.enums import DecisionStatus, MailState, RuleJobState
from app.services.mail.offload_plan import Pick, describe_pick
from app.services.mail.search import GMAIL_VIEWS, SearchError, mail_conditions
from app.services.rules.apply import WAIT_ERRORS, Applier
from app.services.rules.worker import SortingWorker
```

- Add after `OPEN_STATES`:

```python
NO_RULES_MSG = "Pick at least one rule."
ONE_RULE_MSG = "Run on the Inbox takes one rule."
RULE_GONE_MSG = "That rule no longer exists."
RULE_OFF_MSG = "Turn the rule on to run it."
NO_SCOPE_MSG = "Search first, or pick some mail."
VIEW_MSG = "Rules run on mail in Gmail's views only."
NO_MAIL_MSG = "No mail in Gmail matches this search."
EMPTY_INBOX_MSG = "No mail in the Inbox to run this rule on."
# Gmail's views a run can cover (spec §4). Suggested is a list of suggestions, not of mail.
RUN_VIEWS = GMAIL_VIEWS - {"suggested"}
```

- Add after `class RuleJobProblem`:

```python
@dataclass
class RunScope:
    """What a run covers (running rules spec §8): the Inbox (a rule card's Run), a search or a
    pick-all as the view, label and search, or mails or threads picked by hand. Picked threads
    count only their mails in `view` (and `label`)."""

    inbox: bool = False
    view: str = "all"
    label: str | None = None
    q: str = ""
    pick_all: bool = False
    message_ids: list[int] | None = None
    thread_ids: list[str] | None = None


def _check_rules(db: Session, account_id: int, rule_ids: list[int]) -> None:
    found = {
        r.id: r
        for r in db.scalars(
            select(Rule).where(Rule.account_id == account_id, Rule.id.in_(rule_ids))
        )
    }
    for rid in rule_ids:
        if rid not in found:
            raise RuleJobProblem(RULE_GONE_MSG)
        if not found[rid].enabled:
            raise RuleJobProblem(RULE_OFF_MSG)


def _scope_conditions(db: Session, account_id: int, scope: RunScope, now) -> list:
    """Conditions for the mail a run covers, beyond "this mailbox, in Gmail"."""
    if scope.inbox:
        return mail_conditions(db, account_id, view="inbox", now=now)
    if scope.message_ids:
        return [MailMessage.id.in_(scope.message_ids[: MAX_JOB + 1])]
    if scope.view not in RUN_VIEWS:
        raise RuleJobProblem(VIEW_MSG)
    if scope.thread_ids:
        return [
            MailMessage.thread_id.in_(scope.thread_ids),
            *mail_conditions(db, account_id, view=scope.view, label=scope.label, now=now),
        ]
    if scope.pick_all or scope.q.strip():
        return mail_conditions(
            db, account_id, view=scope.view, label=scope.label, q=scope.q, now=now
        )
    raise RuleJobProblem(NO_SCOPE_MSG)


def _search_of(scope: RunScope) -> str:
    """The search a run went through: "" for the Inbox and for a pick by hand."""
    if scope.inbox or scope.message_ids or scope.thread_ids:
        return ""
    return scope.q.strip()


def _describe(db: Session, account_id: int, scope: RunScope, n: int) -> str:
    """What the run covers, in words: "Inbox", "Inbox · from:shop", "3 picked mails"."""
    if scope.inbox:
        return describe_pick(db, account_id, Pick(view="inbox"), n)
    pick = Pick(
        q=_search_of(scope),
        message_ids=scope.message_ids,
        thread_ids=scope.thread_ids,
        view=scope.view,
        label=scope.label,
    )
    return describe_pick(db, account_id, pick, n) or "All mail"
```

- Add four fields at the end of `JobPreview`:

```python
    rule_ids: list[int]
    view: str
    q: str
    filter_text: str
```

- [ ] **Step 4: `start_preview` takes the rules and the scope**

Replace `start_preview` with:

```python
    def start_preview(self, account_id: int, *, rule_ids: list[int], scope: RunScope) -> int:
        """Queue every mail the scope covers, to be decided with `rule_ids` only."""
        now = self.clock()
        rule_ids = list(dict.fromkeys(rule_ids))
        if not rule_ids:
            raise RuleJobProblem(NO_RULES_MSG)
        if scope.inbox and len(rule_ids) != 1:
            raise RuleJobProblem(ONE_RULE_MSG)
        with self.sf() as db:
            _check_rules(db, account_id, rule_ids)
            try:
                conds = _scope_conditions(db, account_id, scope, now)
            except SearchError as e:
                raise RuleJobProblem(str(e)) from None
            base = select(MailMessage.id).where(
                MailMessage.account_id == account_id,
                MailMessage.state == MailState.IN_MAIL,
                *conds,
            )
            open_jobs = list(
                db.scalars(
                    select(RuleJob).where(
                        RuleJob.account_id == account_id, RuleJob.state.in_(OPEN_STATES)
                    )
                )
            )
            if any(j.state == RuleJobState.RUNNING for j in open_jobs):
                raise RuleJobProblem(
                    "Another run of rules for this mailbox isn't finished. Run or stop it first."
                )
            # A preview nobody went on with (its dialog was closed) gives way to the new one.
            for j in open_jobs:
                self._stop_in(db, j)
            found = list(db.scalars(base.order_by(MailMessage.id).limit(MAX_JOB + 1)))
            if len(found) > MAX_JOB:
                raise RuleJobProblem(
                    f"That's more than {MAX_JOB:,} mails. Please narrow the search."
                )
            moved = set(
                db.scalars(
                    select(RuleDecision.message_id)
                    .where(
                        RuleDecision.account_id == account_id,
                        RuleDecision.moved_back_at.is_not(None),
                    )
                    .distinct()
                )
            )
            queued = set(
                db.scalars(select(RuleTodo.message_id).where(RuleTodo.account_id == account_id))
            )
            ids = [i for i in found if i not in moved and i not in queued]
            if not ids:
                raise RuleJobProblem(EMPTY_INBOX_MSG if scope.inbox else NO_MAIL_MSG)
            job = RuleJob(
                account_id=account_id,
                filter_text=_describe(db, account_id, scope, len(ids)),
                total=len(ids),
                rule_ids=rule_ids,
                view="inbox" if scope.inbox else scope.view,
                q=_search_of(scope),
            )
            db.add(job)
            db.flush()
            for mid in ids:
                db.add(
                    RuleTodo(
                        account_id=account_id,
                        message_id=mid,
                        job_id=job.id,
                        next_try_at=now,
                        added_at=now,
                    )
                )
            db.commit()
            jid = job.id
        self.worker.poke()
        return jid
```

- [ ] **Step 5: The preview says which rules and what it ran on**

In `preview`, replace the part from `groups: dict[str, dict] = …` to the `return JobPreview(` call's end with:

```python
            groups: dict[tuple, dict] = defaultdict(lambda: {"count": 0, "examples": []})
            rows = db.execute(
                select(
                    RuleDecision.rule_id,
                    RuleDecision.rule_name,
                    RuleDecision.actions,
                    RuleDecision.why,
                    MailMessage.subject,
                    MailMessage.from_addr,
                )
                .join(MailMessage, MailMessage.id == RuleDecision.message_id)
                .where(RuleDecision.job_id == job_id, RuleDecision.rule_id.is_not(None))
                .order_by(RuleDecision.id)
            ).all()
            for rule_id, name, actions, why, subject, sender in rows:
                g = groups[(rule_id, name)]
                g["count"] += 1
                g["actions"] = actions
                if len(g["examples"]) < EXAMPLES:
                    g["examples"].append({"subject": subject, "sender": sender, "why": why})
            ordered = sorted(groups.items(), key=lambda x: -x[1]["count"])
            return JobPreview(
                job_id=job.id,
                state=job.state,
                total=job.total,
                decided=job.decided,
                planned=counts.get(DecisionStatus.PLANNED, 0),
                per_rule=[{"rule": n, "rule_id": rid, **g} for (rid, n), g in ordered],
                no_rule=counts.get(DecisionStatus.NO_RULE, 0),
                applied=job.applied,
                skipped=job.skipped,
                failed=failed or 0,
                reason=job.reason,
                rule_ids=list(job.rule_ids or []),
                view=job.view,
                q=job.q,
                filter_text=job.filter_text,
            )
```

- [ ] **Step 6: The API takes the new start**

In `backend/app/schemas/rules.py`, replace `JobStartBody` with:

```python
class JobStartBody(AccountBody):
    """Running rules spec §8: the rules, and one of the Inbox (`inbox`, one rule), a search
    (`view`, `label`, a non-empty `q`), a pick-all (the same with `pick_all`), or a pick by
    hand (`message_ids`, or `thread_ids` with `view` and `label`)."""

    rule_ids: list[int] = Field(default_factory=list, max_length=100)
    inbox: bool = False
    view: str = Field("all", max_length=32)
    label: str | None = Field(None, max_length=128)
    q: str = Field("", max_length=1000)
    pick_all: bool = False
    message_ids: list[int] | None = Field(None, max_length=20000)
    thread_ids: list[str] | None = Field(None, max_length=5000)
```

and add to `JobPreviewOut`, before `paused`:

```python
    rule_ids: list[int] = []
    view: str = ""
    q: str = ""
    filter_text: str = ""
```

In `backend/app/api/rules.py`, change `from app.services.rules.jobs import RuleJobProblem` to `from app.services.rules.jobs import RuleJobProblem, RunScope`, and in `start_job` replace the `start_preview(...)` call with:

```python
        jid = request.app.state.rule_jobs.start_preview(
            body.account_id,
            rule_ids=body.rule_ids,
            scope=RunScope(
                inbox=body.inbox,
                view=body.view,
                label=body.label or None,
                q=body.q,
                pick_all=body.pick_all,
                message_ids=body.message_ids,
                thread_ids=body.thread_ids,
            ),
        )
```

- [ ] **Step 7: Run the tests to check they pass**

Run: `cd backend && uv run pytest tests/test_rules_running.py tests/test_rules_jobs.py tests/test_rules_api.py tests/test_rules_worker.py -q`
Expected: PASS.

- [ ] **Step 8: Run the full backend suite and lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/rules/jobs.py backend/app/schemas/rules.py backend/app/api/rules.py backend/tests/test_rules_running.py backend/tests/test_rules_jobs.py backend/tests/test_rules_api.py
git commit -m "feat(rules): start a run with its rules on the Inbox, a search, a pick-all or a pick

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The match list, and approving it

**Files:**
- Modify: `backend/app/services/rules/jobs.py` (`Match`, `matches`, `run`, `JobPreview.left_out`, `skip_reasons`)
- Modify: `backend/app/schemas/rules.py` (`MatchOut`, `JobRunBody`, `JobPreviewOut`)
- Modify: `backend/app/api/rules.py` (`GET /api/rule-jobs/{job_id}/matches`, `run_job`)
- Modify: `backend/tests/test_rules_running.py`, `backend/tests/test_rules_jobs.py`, `backend/tests/test_rules_api.py`

**Interfaces:**
- Consumes: Task 2's `RunScope`, `start_preview`, `jobs_for`, `covered`, `_rule_id`; Task 1's test helpers; round 1's `"offload"` rule action (`store.ACTION_KINDS`) and `app.services.mail.offload_plan.chunked`.
- Produces:
  - `jobs.Match(id, message_id, thread_id, subject, from_addr, date, rule_id, rule_name, actions, why)` (dataclass)
  - `RuleJobs.matches(account_id, job_id, *, rule_id=None, before_id=None, limit=MATCH_PAGE) -> list[Match]`, with `MATCH_PAGE = 50`, `MAX_MATCH_PAGE = 200`
  - `RuleJobs.run(account_id, job_id, confirm: int | None = None, *, left_out: Collection[int] = (), only: Collection[int] | None = None) -> None`
  - `jobs.HEAVY_KINDS = ("offload", "delete_forever")`, `jobs.LEFT_OUT_MSG = "You left it out."`
  - `JobPreview.left_out: int`, `JobPreview.skip_reasons: list[dict]` (`{"reason", "count"}`)
  - `GET /api/rule-jobs/{job_id}/matches?account_id=&rule_id=&before_id=&limit=` gives `list[MatchOut]`
  - `POST /api/rule-jobs/{job_id}/run` body `{account_id, left_out?: int[], only?: int[] | null, confirm?: int | null}`
  - Test helper `decided(gbox, worker, jobs, rule_ids, scope) -> int` in `tests/test_rules_running.py`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_rules_running.py`, add `from app.models.enums import DecisionStatus, RuleJobState` to the imports, change the jobs import to `from app.services.rules.jobs import CHANGED_MSG, RuleJobProblem, RuleJobs, RunScope`, and add at the end:

```python
async def decided(gbox, worker, jobs, rule_ids, scope) -> int:
    """Start a run and decide all of it."""
    jid = jobs.start_preview(gbox.acc.id, rule_ids=rule_ids, scope=scope)
    while await worker.run_once(gbox.acc.id):
        pass
    return jid


async def test_the_match_list_pages_and_filters_by_rule(gbox):
    news, shops = rule(gbox, "News"), rule(gbox, "Shops")
    for i in range(3):
        gbox.add(f"News {i}")
    gbox.add("Shops one")
    gbox.add("Nothing here")
    worker, jobs = jobs_for(gbox)
    scope = RunScope(view="inbox", q="", pick_all=True)
    jid = await decided(gbox, worker, jobs, [news, shops], scope)
    first = jobs.matches(gbox.acc.id, jid, limit=3)
    rest = jobs.matches(gbox.acc.id, jid, before_id=first[-1].id, limit=3)
    ids = [m.id for m in first + rest]
    assert (len(first), len(rest)) == (3, 1) and ids == sorted(ids, reverse=True)
    assert {m.subject for m in first + rest} == {"News 0", "News 1", "News 2", "Shops one"}
    (only,) = jobs.matches(gbox.acc.id, jid, rule_id=shops)
    assert (only.subject, only.rule_name, only.why, only.actions) == (
        "Shops one",
        "Shops",
        "subject mentions Shops",
        ARCHIVE,
    )
    assert only.from_addr == "friend@example.com" and only.thread_id
    with pytest.raises(RuleJobProblem):
        jobs.matches(gbox.acc.id + 1, jid)


async def test_approving_leaves_out_the_unticked_and_runs_the_rest(gbox):
    news = rule(gbox, "News")
    kept, changed, moved = gbox.add("News kept"), gbox.add("News changed"), gbox.add("News moved")
    worker, jobs = jobs_for(gbox)
    jid = await decided(gbox, worker, jobs, [news], RunScope(inbox=True))
    ids = {m.subject: m.id for m in jobs.matches(gbox.acc.id, jid)}
    with gbox.sf() as db:
        db.get(MailMessage, moved.id).labels = ["INBOX", "STARRED"]  # changed since deciding
        db.commit()
    jobs.run(gbox.acc.id, jid, left_out=[ids["News kept"], 999_999])  # a stranger id is ignored
    await jobs.wait(jid)
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.state, p.applied, p.skipped, p.left_out) == (RuleJobState.DONE, 1, 1, 1)
    assert p.skip_reasons == [{"reason": CHANGED_MSG, "count": 1}]
    assert "INBOX" in gbox.fake.messages[kept.provider_id].labels
    assert "INBOX" not in gbox.fake.messages[changed.provider_id].labels
    with gbox.sf() as db:
        d = db.get(RuleDecision, ids["News kept"])
        assert (d.status, d.reason) == (DecisionStatus.REJECTED, "You left it out.")


async def test_only_runs_just_the_ticked_matches(gbox):
    news = rule(gbox, "News")
    rows = [gbox.add(f"News {i}") for i in range(3)]
    worker, jobs = jobs_for(gbox)
    jid = await decided(gbox, worker, jobs, [news], RunScope(inbox=True))
    ids = {m.subject: m.id for m in jobs.matches(gbox.acc.id, jid)}
    jobs.run(gbox.acc.id, jid, only=[ids["News 1"]])
    await jobs.wait(jid)
    p = jobs.preview(gbox.acc.id, jid)
    assert (p.applied, p.left_out) == (1, 2)
    labels = [gbox.fake.messages[r.provider_id].labels for r in rows]
    assert ["INBOX" in x for x in labels] == [True, False, True]


async def test_the_typed_count_is_asked_only_for_picked_offloads(gbox, monkeypatch):
    news = rule(gbox, "News")
    junk = rule(gbox, "Junk", actions=[{"kind": "offload"}])
    for subject in ("News a", "Junk a", "Junk b"):
        gbox.add(subject)
    applied: list[int] = []

    async def apply(did):
        applied.append(did)
        with gbox.sf() as db:
            db.get(RuleDecision, did).status = DecisionStatus.APPLIED
            db.commit()
        return DecisionStatus.APPLIED

    monkeypatch.setattr(gbox.applier, "apply", apply)
    worker, jobs = jobs_for(gbox)
    scope = RunScope(view="inbox", q="", pick_all=True)
    jid = await decided(gbox, worker, jobs, [news, junk], scope)
    ids = {m.subject: m.id for m in jobs.matches(gbox.acc.id, jid)}
    with pytest.raises(
        RuleJobProblem, match=r"^The number you typed doesn't match the 2 mails to offload\.$"
    ):
        jobs.run(gbox.acc.id, jid)
    with pytest.raises(RuleJobProblem, match=r"doesn't match the 1 mail to offload\.$"):
        jobs.run(gbox.acc.id, jid, 2, left_out=[ids["Junk b"]])
    jobs.run(gbox.acc.id, jid, 1, left_out=[ids["Junk b"]])
    await jobs.wait(jid)
    assert sorted(applied) == sorted([ids["News a"], ids["Junk a"]])
```

In `backend/tests/test_rules_jobs.py`, in `test_preview_then_run_applies_with_the_typed_count`, replace

```python
    with pytest.raises(RuleJobProblem, match="doesn't match"):
        jobs.run(gbox.acc.id, jid, 2)
    jobs.run(gbox.acc.id, jid, 3)
```

with `jobs.run(gbox.acc.id, jid)  # archive needs no typed count`, and rename the test `test_preview_then_run_applies_without_a_typed_count_for_archive`.

In `backend/tests/test_rules_api.py`, add after `test_starting_a_run_needs_rules_and_a_search_or_a_pick`:

```python
def test_the_match_list_and_approving_it_from_the_api(
    auth_client, acc, db, layout, make_account
):
    from app.devtools.fake_gmail import make_mail
    from app.models import RuleJob
    from app.models.enums import RuleJobState
    from tests.rules_helpers import keep

    rid = _rule_id(auth_client, acc)
    rows = [keep(db, layout, acc, make_mail(f"Digest {i}")) for i in range(2)]
    job = RuleJob(
        account_id=acc.id, total=2, decided=2, state=RuleJobState.PREVIEWED, rule_ids=[rid]
    )
    db.add(job)
    db.flush()
    for r in rows:
        db.add(
            RuleDecision(
                account_id=acc.id,
                message_id=r.id,
                rule_id=rid,
                rule_version=1,
                rule_name="Newsletters",
                how="ai",
                why="a digest",
                actions=RULE["actions"],
                labels_seen=["INBOX"],
                status="planned",
                job_id=job.id,
            )
        )
    db.commit()
    B = {"account_id": acc.id}
    url = f"/api/rule-jobs/{job.id}"
    got = auth_client.get(f"{url}/matches", params=B).json()
    assert [m["subject"] for m in got] == ["Digest 1", "Digest 0"]
    assert (got[0]["why"], got[0]["rule_name"], got[0]["rule_id"]) == ("a digest", "Newsletters", rid)
    after = auth_client.get(f"{url}/matches", params={**B, "before_id": got[0]["id"]}).json()
    assert [m["subject"] for m in after] == ["Digest 0"]
    assert auth_client.get(f"{url}/matches", params={**B, "rule_id": rid + 1}).json() == []
    bob = {"account_id": make_account("bob@example.com").id}
    assert auth_client.get(f"{url}/matches", params=bob).status_code in (400, 404)
    r = auth_client.post(f"{url}/run", json={**B, "left_out": [m["id"] for m in got]})
    assert r.status_code == 202, r.text
    for _ in range(50):  # the run is a task on the app's loop: give it a moment
        p = auth_client.get(url, params=B).json()
        if p["state"] == "DONE":
            break
        time.sleep(0.05)
    assert (p["state"], p["applied"], p["left_out"]) == ("DONE", 0, 2)
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_rules_running.py tests/test_rules_jobs.py tests/test_rules_api.py -q`
Expected: FAIL. `RuleJobs` has no `matches`, `run` takes no `left_out`, and archive still asks for the typed count.

- [ ] **Step 3: The match list and the approval in `jobs.py`**

In `backend/app/services/rules/jobs.py`:
- Add `from collections.abc import Collection` and `from datetime import datetime` to the imports, and change the offload-plan import to `from app.services.mail.offload_plan import Pick, chunked, describe_pick`.
- Add after `RUN_VIEWS`:

```python
LEFT_OUT_MSG = "You left it out."
# Actions that take mail out of Gmail: approving them needs the typed count (spec §5).
HEAVY_KINDS = ("offload", "delete_forever")
MATCH_PAGE = 50
MAX_MATCH_PAGE = 200


def _mails(n: int) -> str:
    return f"{n:,} {'mail' if n == 1 else 'mails'}"


def _heavy_words(kinds: set[str]) -> str:
    if kinds == {"offload"}:
        return "offload"
    if kinds == {"delete_forever"}:
        return "delete forever"
    return "offload or delete forever"
```

- Add after `class JobPreview`:

```python
@dataclass
class Match:
    """One row of the match list (running rules spec §5): a planned change and its mail."""

    id: int
    message_id: int
    thread_id: str
    subject: str
    from_addr: str
    date: datetime
    rule_id: int | None
    rule_name: str
    actions: list[dict]
    why: str
```

- Add two fields at the end of `JobPreview`:

```python
    left_out: int
    skip_reasons: list[dict]
```

- In `preview`, after `counts = dict(...)`, add:

```python
            left_out = db.scalar(
                select(func.count()).where(
                    RuleDecision.job_id == job_id,
                    RuleDecision.status == DecisionStatus.REJECTED,
                    RuleDecision.reason == LEFT_OUT_MSG,
                )
            )
            skips = db.execute(
                select(RuleDecision.reason, func.count())
                .where(
                    RuleDecision.job_id == job_id, RuleDecision.status == DecisionStatus.SKIPPED
                )
                .group_by(RuleDecision.reason)
                .order_by(func.count().desc())
            ).all()
```

and add to the `JobPreview(...)` call, after `filter_text=job.filter_text,`:

```python
                left_out=left_out or 0,
                skip_reasons=[{"reason": r or "", "count": n} for r, n in skips],
```

- Add after `preview`:

```python
    def matches(
        self,
        account_id: int,
        job_id: int,
        *,
        rule_id: int | None = None,
        before_id: int | None = None,
        limit: int = MATCH_PAGE,
    ) -> list[Match]:
        """The match list (running rules spec §5): the run's planned changes, newest first,
        a page at a time. `rule_id` keeps one rule's."""
        with self.sf() as db:
            self._job(db, account_id, job_id)
            q = (
                select(RuleDecision, MailMessage)
                .join(MailMessage, MailMessage.id == RuleDecision.message_id)
                .where(
                    RuleDecision.job_id == job_id, RuleDecision.status == DecisionStatus.PLANNED
                )
            )
            if rule_id is not None:
                q = q.where(RuleDecision.rule_id == rule_id)
            if before_id is not None:
                q = q.where(RuleDecision.id < before_id)
            page = max(1, min(limit, MAX_MATCH_PAGE))
            rows = db.execute(q.order_by(RuleDecision.id.desc()).limit(page)).all()
            return [
                Match(
                    id=d.id,
                    message_id=m.id,
                    thread_id=m.thread_id,
                    subject=m.subject or "",
                    from_addr=m.from_addr or "",
                    date=m.date,
                    rule_id=d.rule_id,
                    rule_name=d.rule_name,
                    actions=list(d.actions or []),
                    why=d.why or "",
                )
                for d, m in rows
            ]
```

- Replace `run` with:

```python
    def run(
        self,
        account_id: int,
        job_id: int,
        confirm: int | None = None,
        *,
        left_out: Collection[int] = (),
        only: Collection[int] | None = None,
    ) -> None:
        """Approve the match list (running rules spec §5). The matches left out (every one
        not in `only`, when it is given) become rejected; the rest are made. `confirm` must be
        the number of picked mails that would be offloaded or deleted forever, if any."""
        with self.sf() as db:
            job = self._job(db, account_id, job_id)
            if job.state != RuleJobState.PREVIEWED:
                raise RuleJobProblem("Wait until the preview is finished.")
            planned = dict(
                db.execute(
                    select(RuleDecision.id, RuleDecision.actions).where(
                        RuleDecision.job_id == job_id,
                        RuleDecision.status == DecisionStatus.PLANNED,
                    )
                ).all()
            )
            out = set(planned) - set(only) if only is not None else set(left_out) & set(planned)
            heavy, kinds = 0, set()
            for did, actions in planned.items():
                found = {a.get("kind") for a in actions or []} & set(HEAVY_KINDS)
                if did not in out and found:
                    heavy += 1
                    kinds |= found
            if heavy and confirm != heavy:
                raise RuleJobProblem(
                    f"The number you typed doesn't match the {_mails(heavy)} "
                    f"to {_heavy_words(kinds)}."
                )
            for part in chunked(sorted(out)):
                db.execute(
                    update(RuleDecision)
                    .where(RuleDecision.id.in_(part))
                    .values(status=DecisionStatus.REJECTED, reason=LEFT_OUT_MSG)
                    .execution_options(synchronize_session=False)
                )
            job.state = RuleJobState.RUNNING
            db.commit()
        try:
            self._start(job_id)
        except Exception:
            with self.sf() as db:
                db.get(RuleJob, job_id).state = RuleJobState.PREVIEWED
                db.commit()
            raise
```

- [ ] **Step 4: The API**

In `backend/app/schemas/rules.py`, replace `JobRunBody` with:

```python
class JobRunBody(AccountBody):
    """Approving the match list (running rules spec §8). `left_out`: the unticked matches.
    `only`: after Pick all was unticked, the matches ticked by hand; every other one is left
    out. `confirm` is needed only when picked mail would be offloaded or deleted forever."""

    left_out: list[int] = Field(default_factory=list, max_length=20000)
    only: list[int] | None = Field(None, max_length=20000)
    confirm: int | None = None


class MatchOut(BaseModel):
    id: int
    message_id: int
    thread_id: str
    subject: str
    from_addr: str
    date: UtcDatetime
    rule_id: int | None
    rule_name: str
    actions: list[dict]
    why: str
```

and add to `JobPreviewOut`, before `paused`:

```python
    left_out: int = 0
    skip_reasons: list[dict] = []
```

In `backend/app/api/rules.py`:
- Add `MatchOut` to the `app.schemas.rules` import, and change the jobs import to `from app.services.rules.jobs import MATCH_PAGE, RuleJobProblem, RunScope`.
- Add after `job` (the `GET /api/rule-jobs/{job_id}` endpoint):

```python
@router.get("/api/rule-jobs/{job_id}/matches", response_model=list[MatchOut])
def job_matches(
    job_id: int,
    account_id: int,
    request: Request,
    rule_id: int | None = None,
    before_id: int | None = None,
    limit: int = MATCH_PAGE,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    """The run's match list, newest first (running rules spec §8)."""
    account_or_404(db, account_id)
    try:
        found = request.app.state.rule_jobs.matches(
            account_id, job_id, rule_id=rule_id, before_id=before_id, limit=limit
        )
    except PROBLEMS as e:
        raise _problem(e) from e
    return [MatchOut(**asdict(m)) for m in found]
```

- In `run_job`, the call becomes:

```python
        request.app.state.rule_jobs.run(
            body.account_id, job_id, body.confirm, left_out=body.left_out, only=body.only
        )
```

- [ ] **Step 5: Run the tests to check they pass**

Run: `cd backend && uv run pytest tests/test_rules_running.py tests/test_rules_jobs.py tests/test_rules_api.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full backend suite and lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/rules/jobs.py backend/app/schemas/rules.py backend/app/api/rules.py backend/tests/test_rules_running.py backend/tests/test_rules_jobs.py backend/tests/test_rules_api.py
git commit -m "feat(rules): the match list of a run, approved with the mails left out

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The Run rules window shows the match list; "Run rules on…" goes

**Files:**
- Create: `frontend/src/lib/ruleRuns.ts`, `frontend/src/lib/ruleRuns.test.ts`
- Modify: `frontend/src/api/rules.ts` (`JobPreview`, `RuleMatch`, `RunScope`, `RunStart`, `useRuleMatches`)
- Modify: `frontend/src/hooks/useRules.ts` (`useStartRuleJob`, `useRunRuleJob`, `RULE_KEYS`)
- Modify: `frontend/src/components/mail/MailList.tsx` (export `PickAllBox`)
- Modify: `frontend/src/components/mail/RunRulesDialog.tsx` (rewritten)
- Modify: `frontend/src/components/mail/MakeRuleDialog.tsx` (runs only the saved rule)
- Modify: `frontend/src/components/mail/MailActions.tsx` (no "Run rules on…")
- Modify: `frontend/src/pages/MailRulesPage.tsx` (no "Run rules on…" in the footer)
- Modify: `frontend/src/lib/mail.ts` (remove `runRulesStart`)
- Modify: `frontend/src/components/mail/MailAi.test.tsx`

**Interfaces:**
- Consumes:
  - Tasks 2–3's API: `POST /api/rule-jobs` with `{account_id, rule_ids, ...scope}`, `GET /api/rule-jobs/{id}/matches`, `POST /api/rule-jobs/{id}/run` with `{account_id, left_out, only?, confirm?}`, and the new `JobPreview` fields.
  - Round 1: `PickAllBox` and `type PickState` in `components/mail/MailList.tsx`; `viewKind(view)` and `mailView(param)` in `lib/mail.ts`; `RuleAction` with kinds `offload` and `delete_forever`, and `describeAction` in `api/rules.ts`; `MailHeaderActions({ account, q, view, label, scope })` in `MailActions.tsx`.
- Produces:
  - In `api/rules.ts`: `type RunScope = { inbox: true } | { view: string; label?: string; q: string; pick_all?: true } | { view: string; label?: string; thread_ids: string[] }`; `type RunStart = { rule_ids: number[]; scope: RunScope }`; `type RuleMatch`; `MATCH_PAGE = 50`; `useRuleMatches(accountId: number, jobId: number | null, ruleId: number | null)`; `JobPreview` with `rule_ids`, `view`, `q`, `filter_text`, `left_out`, `skip_reasons`, and `per_rule[].rule_id`.
  - In `hooks/useRules.ts`: `useStartRuleJob()` mutating `{ accountId, start: RunStart }`; `useRunRuleJob()` mutating `{ accountId, jobId, leftOut?, only?, confirm? }`.
  - In `lib/ruleRuns.ts`: `mails(n)`, `HEAVY_KINDS`, `type RulePick = { actions: RuleAction[]; picked: number }`, `actionText(a, n)`, `approveText(rules: RulePick[])`, `wouldText(rule, actions, n)`, `ruleLine(rule, actions, n)`, `heavyKinds(rules: RulePick[]): { count: number; kinds: Set<string> }`, `confirmLabel(kinds, n)`, `olderMailScope(senders, view, label, q): RunScope`.
  - `RunRulesDialog({ account, onClose } & ({ start: RunStart } | { jobId: number }))`.
  - `MailHeaderActions({ account, q, view, label, scope })` without "Run rules on…".

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/src/lib/ruleRuns.test.ts
import { describe, expect, it } from "vitest";

import { actionText, approveText, confirmLabel, heavyKinds, olderMailScope, ruleLine, wouldText } from "./ruleRuns";

describe("running rules, in words", () => {
  it("names one action and the count, else Apply N changes", () => {
    expect(approveText([{ actions: [{ kind: "archive" }], picked: 412 }])).toBe("Archive 412 mails");
    expect(approveText([{ actions: [{ kind: "label", label: "Receipts" }], picked: 1 }])).toBe("Add the label Receipts to 1 mail");
    expect(approveText([{ actions: [{ kind: "label", label: "Marketing" }, { kind: "archive" }], picked: 2 }])).toBe("Apply 2 changes");
    expect(approveText([{ actions: [{ kind: "archive" }], picked: 412 }, { actions: [{ kind: "star" }], picked: 30 }])).toBe("Apply 442 changes");
    expect(approveText([{ actions: [{ kind: "archive" }], picked: 3 }, { actions: [{ kind: "star" }], picked: 0 }])).toBe("Archive 3 mails");
    expect(approveText([{ actions: [{ kind: "archive" }], picked: 0 }])).toBe("Archive 0 mails");
    expect(actionText({ kind: "mark_read" }, 5)).toBe("Mark 5 mails read");
    expect(actionText({ kind: "star" }, 1)).toBe("Star 1 mail");
    expect(actionText({ kind: "offload" }, 120)).toBe("Offload 120 mails");
    expect(actionText({ kind: "delete_forever" }, 2)).toBe("Delete 2 mails forever");
  });

  it("says what a rule would do", () => {
    expect(wouldText("Marketing", [{ kind: "archive" }], 412)).toBe("Marketing would archive 412 mails.");
    expect(wouldText("Marketing", [{ kind: "label", label: "Marketing" }, { kind: "archive" }], 2)).toBe("Marketing would change 2 mails: Label Marketing, Archive.");
    expect(ruleLine("Marketing", [{ kind: "archive" }], 412)).toBe("Marketing · archive · 412");
    expect(ruleLine("Receipts", [{ kind: "label", label: "Receipts" }], 30)).toBe("Receipts · label Receipts · 30");
  });

  it("asks for the typed count only for picked Offload and Delete forever", () => {
    expect(heavyKinds([{ actions: [{ kind: "archive" }], picked: 9 }])).toEqual({ count: 0, kinds: new Set() });
    const h = heavyKinds([
      { actions: [{ kind: "offload" }], picked: 120 },
      { actions: [{ kind: "archive" }], picked: 5 },
      { actions: [{ kind: "delete_forever" }], picked: 0 },
    ]);
    expect(h).toEqual({ count: 120, kinds: new Set(["offload"]) });
    expect(confirmLabel(h.kinds, h.count)).toBe("Type 120 to offload these mails");
    expect(confirmLabel(new Set(["delete_forever"]), 1)).toBe("Type 1 to delete these mails forever");
    expect(confirmLabel(new Set(["offload", "delete_forever"]), 3)).toBe("Type 3 to offload or delete forever these mails");
  });

  it("runs a rule made from mail on its sender, the list on screen, or the Inbox", () => {
    expect(olderMailScope(["shop@example.com"], "inbox", "", "")).toEqual({ view: "all", q: "from:shop@example.com" });
    expect(olderMailScope(["a@x.com", "b@x.com"], "all", "Label_2", "older_than:1y")).toEqual({ view: "all", label: "Label_2", q: "older_than:1y", pick_all: true });
    expect(olderMailScope([], "archived", "", "")).toEqual({ view: "archived", q: "", pick_all: true });
    expect(olderMailScope([], "all", "", "")).toEqual({ inbox: true });
    expect(olderMailScope([], "suggested", "", "x")).toEqual({ inbox: true });
    expect(olderMailScope([], "offloaded", "", "x")).toEqual({ inbox: true });
  });
});
```

In `frontend/src/components/mail/MailAi.test.tsx`:
- Delete these tests: "run rules shows the preview and runs after the typed count", "says plainly why mail was left as it was, without sending it to Suggested", and "reopens a run that is already there instead of starting another".
- Add after `const RULES = …`:

```tsx
const JOB = {
  job_id: 9,
  state: "PREVIEWED",
  total: 4,
  decided: 4,
  planned: 3,
  no_rule: 1,
  applied: 0,
  skipped: 0,
  failed: 0,
  reason: null,
  view: "inbox",
  q: "",
  rule_ids: [5],
  filter_text: "Inbox",
  left_out: 0,
  skip_reasons: [],
  per_rule: [{ rule: "Newsletters", rule_id: 5, count: 3, actions: [{ kind: "archive" }], examples: [] }],
};
const NEWS = { rule_id: 5, rule_name: "Newsletters", actions: [{ kind: "archive" }] };
const match = (id: number, subject: string, rule: { rule_id: number; rule_name: string; actions: unknown[] } = NEWS) => ({
  id,
  message_id: id + 100,
  thread_id: `t${id}`,
  subject,
  from_addr: "news@example.com",
  date: "2026-10-01T09:00:00Z",
  why: "a digest",
  ...rule,
});
const INBOX_RUN = { rule_ids: [5], scope: { inbox: true as const } };
const runBody = (calls: Call[]) => calls.find((c) => c.path === "/api/rule-jobs/9/run")?.body;
```

  and change the utils import to `import { ACCOUNT, type Call, json, mockApi, renderApp } from "../../test/utils";`.
- In "stops the job when the dialog was closed before the job had started" and "closing a preview stops it, so it never blocks the next run; a running job keeps going", every `start={{ q: "", view: "inbox" }}` becomes `start={INBOX_RUN}`. In the second one, `const previewed = {…}` becomes `const previewed = { ...JOB, planned: 1 };` and both `mockApi` calls gain `"GET /api/rule-jobs/9/matches": [],`.
- In "makes a rule from picked mail, saves it, then runs it on the sender's older mail", the expected start body becomes `{ account_id: ACCOUNT.id, rule_ids: [8], view: "all", q: "from:shop@example.com" }`.
- In "runs a rule from several senders on the list the reader showed, never on a view the rules can't use", rename it "runs a rule from several senders on the list the reader showed, or on the Inbox from a list rules can't use", and the two expected bodies become `{ rule_ids: [8], view: "all", label: "Label_2", q: "older_than:1y", pick_all: true }` and `{ rule_ids: [8], inbox: true }`.
- Add a new describe at the end of the file:

```tsx
describe("the Run rules window", () => {
  it("starts with every matched mail picked, and unticking one leaves it out", async () => {
    const { calls } = mockApi({
      "POST /api/rule-jobs": { job_id: 9 },
      "GET /api/rule-jobs/9": JOB,
      "GET /api/rule-jobs/9/matches": [match(13, "Digest three"), match(12, "Digest two"), match(11, "Digest one")],
      "POST /api/rule-jobs/9/run": { job_id: 9 },
    });
    renderApp(<RunRulesDialog account={ACCOUNT} start={INBOX_RUN} onClose={() => {}} />);
    expect(await screen.findByText("Newsletters would archive 3 mails. 1 didn't fit.")).toBeInTheDocument();
    expect(screen.getByText("On: Inbox")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/rule-jobs")?.body).toEqual({ account_id: ACCOUNT.id, rule_ids: [5], inbox: true });
    const all = screen.getByRole("checkbox", { name: "Pick all" });
    expect(all).toBeChecked();
    expect(await screen.findByRole("checkbox", { name: "Pick Digest two" })).toBeChecked();
    expect(screen.queryByLabelText(/^Type /)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick Digest two" }));
    expect(all).toBePartiallyChecked();
    expect(screen.getByRole("checkbox", { name: "Pick Digest three" })).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Archive 2 mails" }));
    await waitFor(() => expect(runBody(calls)).toEqual({ account_id: ACCOUNT.id, left_out: [12] }));
  });

  it("unticking Pick all leaves nothing picked; ticked rows are sent as the only ones", async () => {
    const { calls } = mockApi({
      "GET /api/rule-jobs/9": JOB,
      "GET /api/rule-jobs/9/matches": [match(13, "Digest three"), match(12, "Digest two"), match(11, "Digest one")],
      "POST /api/rule-jobs/9/run": { job_id: 9 },
    });
    renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    const all = await screen.findByRole("checkbox", { name: "Pick all" });
    await screen.findByRole("checkbox", { name: "Pick Digest one" });
    await userEvent.click(all);
    expect(all).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Pick Digest one" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Archive 0 mails" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick Digest one" }));
    expect(all).toBePartiallyChecked();
    await userEvent.click(screen.getByRole("button", { name: "Archive 1 mail" }));
    await waitFor(() => expect(runBody(calls)).toEqual({ account_id: ACCOUNT.id, left_out: [], only: [11] }));
  });

  it("with several rules: a line per rule, a chip per row, a filter that keeps the picks, and Apply N changes", async () => {
    const two = {
      ...JOB,
      rule_ids: [5, 6],
      per_rule: [
        { rule: "Newsletters", rule_id: 5, count: 2, actions: [{ kind: "archive" }], examples: [] },
        { rule: "Receipts", rule_id: 6, count: 1, actions: [{ kind: "label", label: "Receipts" }], examples: [] },
      ],
    };
    const RECEIPTS = { rule_id: 6, rule_name: "Receipts", actions: [{ kind: "label", label: "Receipts" }] };
    const { calls } = mockApi({
      "POST /api/rule-jobs": { job_id: 9 },
      "GET /api/rule-jobs/9": two,
      "GET /api/rule-jobs/9/matches": ({ url }: { url: URL }) =>
        url.searchParams.get("rule_id") === "6" ? [match(13, "Bill", RECEIPTS)] : [match(13, "Bill", RECEIPTS), match(12, "Digest two"), match(11, "Digest one")],
      "POST /api/rule-jobs/9/run": { job_id: 9 },
    });
    renderApp(<RunRulesDialog account={ACCOUNT} start={{ rule_ids: [5, 6], scope: { view: "inbox", q: "from:shop" } }} onClose={() => {}} />);
    const lines = await screen.findByRole("list", { name: "What each rule would do" });
    expect(within(lines).getByText("Newsletters · archive · 2")).toBeInTheDocument();
    expect(within(lines).getByText("Receipts · label Receipts · 1")).toBeInTheDocument();
    expect(screen.getByText("1 didn't fit.")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/rule-jobs")?.body).toEqual({ account_id: ACCOUNT.id, rule_ids: [5, 6], view: "inbox", q: "from:shop" });
    const list = screen.getByRole("list", { name: "Matched mail" });
    expect(await within(list).findByText("Receipts")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply 3 changes" })).toBeEnabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick Digest one" }));
    expect(screen.getByRole("button", { name: "Apply 2 changes" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Show"), "Receipts");
    await waitFor(() => expect(screen.queryByRole("checkbox", { name: "Pick Digest two" })).not.toBeInTheDocument());
    expect(calls.some((c) => c.path === "/api/rule-jobs/9/matches" && c.url.searchParams.get("rule_id") === "6")).toBe(true);
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick Bill" }));
    await userEvent.click(screen.getByRole("button", { name: "Archive 1 mail" }));
    await waitFor(() => expect(runBody(calls)).toEqual({ account_id: ACCOUNT.id, left_out: [11, 13] }));
  });

  it("asks for the typed count only for picked offloads", async () => {
    const mixed = {
      ...JOB,
      rule_ids: [5, 7],
      per_rule: [
        { rule: "Junk", rule_id: 7, count: 2, actions: [{ kind: "offload" }], examples: [] },
        { rule: "Newsletters", rule_id: 5, count: 1, actions: [{ kind: "archive" }], examples: [] },
      ],
    };
    const JUNK = { rule_id: 7, rule_name: "Junk", actions: [{ kind: "offload" }] };
    const { calls } = mockApi({
      "GET /api/rule-jobs/9": mixed,
      "GET /api/rule-jobs/9/matches": [match(13, "Junk one", JUNK), match(12, "Junk two", JUNK), match(11, "Digest one")],
      "POST /api/rule-jobs/9/run": { job_id: 9 },
    });
    renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByLabelText("Type 2 to offload these mails")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply 3 changes" })).toBeDisabled();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick Junk two" }));
    await userEvent.type(screen.getByLabelText("Type 1 to offload these mails"), "1");
    await userEvent.click(screen.getByRole("button", { name: "Apply 2 changes" }));
    await waitFor(() => expect(runBody(calls)).toEqual({ account_id: ACCOUNT.id, left_out: [12], confirm: 1 }));
  });

  it("reopens a run with its match list, and Show more loads the next page", async () => {
    const page1 = Array.from({ length: 50 }, (_, i) => match(200 - i, `Digest ${200 - i}`));
    const { calls } = mockApi({
      "GET /api/rule-jobs/9": { ...JOB, planned: 51, per_rule: [{ ...JOB.per_rule[0], count: 51 }] },
      "GET /api/rule-jobs/9/matches": ({ url }: { url: URL }) => (url.searchParams.get("before_id") === "151" ? [match(7, "Digest 7")] : page1),
    });
    renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByRole("checkbox", { name: "Pick Digest 151" })).toBeChecked();
    expect(calls.find((c) => c.path === "/api/rule-jobs/9/matches")?.url.searchParams.get("limit")).toBe("50");
    await userEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(await screen.findByRole("checkbox", { name: "Pick Digest 7" })).toBeChecked();
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Archive 51 mails" })).toBeEnabled();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("says when nothing fits, with Close", async () => {
    const none = { ...JOB, planned: 0, no_rule: 4, per_rule: [] };
    mockApi({ "GET /api/rule-jobs/9": none });
    const r = renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByText("No mail fits this rule.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    r.unmount();
    mockApi({ "GET /api/rule-jobs/9": { ...none, rule_ids: [5, 6] } });
    renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByText("No mail fits these rules.")).toBeInTheDocument();
  });

  it("ends with applied, skipped and why, failed and left out", async () => {
    const reason = "It changed since the preview, so it was left as it is.";
    const done = { ...JOB, state: "DONE", planned: 0, applied: 1, skipped: 2, failed: 0, left_out: 3, skip_reasons: [{ reason, count: 2 }] };
    mockApi({ "GET /api/rule-jobs/9": done });
    const r = renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByText("Done. Applied 1, skipped 2, failed 0, left out 3.")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Why mail was skipped" })).getByText(`${reason} (2)`)).toBeInTheDocument();
    r.unmount();
    mockApi({ "GET /api/rule-jobs/9": { ...done, state: "STOPPED", skipped: 1, left_out: 0, skip_reasons: [] } });
    renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByText("Stopped. Applied 1, skipped 1, failed 0, left out 0.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd frontend && npm test -- --run src/lib/ruleRuns.test.ts src/components/mail/MailAi.test.tsx`
Expected: FAIL. `./ruleRuns` doesn't exist, and the dialog still shows the old preview and "Change N mails".

- [ ] **Step 3: The words of a run**

```ts
// frontend/src/lib/ruleRuns.ts
import { describeAction, type RuleAction, type RunScope } from "../api/rules";
import { viewKind } from "./mail";

/** "1 mail", "1,284 mails". */
export const mails = (n: number) => `${n.toLocaleString()} ${n === 1 ? "mail" : "mails"}`;
const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** Actions that take mail out of Gmail: approving them needs the typed count (running rules spec §5). */
export const HEAVY_KINDS: readonly string[] = ["offload", "delete_forever"];

/** One rule of a run, with how many of its matches are picked. */
export type RulePick = { actions: RuleAction[]; picked: number };

/** One action done to `n` mails, as the approve button says it. */
export function actionText(a: RuleAction, n: number): string {
  switch (a.kind) {
    case "archive":
      return `Archive ${mails(n)}`;
    case "star":
      return `Star ${mails(n)}`;
    case "mark_read":
      return `Mark ${mails(n)} read`;
    case "label":
      return `Add the label ${a.label} to ${mails(n)}`;
    case "offload":
      return `Offload ${mails(n)}`;
    case "delete_forever":
      return `Delete ${mails(n)} forever`;
  }
}

/** The approve button (spec §5): the action and the count when the picked mails all belong to
 * one rule with one action, else "Apply N changes" (one change per mail). */
export function approveText(rules: RulePick[]): string {
  const live = rules.filter((r) => r.picked > 0);
  const one = live.length === 1 ? live[0] : live.length === 0 && rules.length === 1 ? rules[0] : null;
  if (one && one.actions.length === 1) return actionText(one.actions[0], one.picked);
  const n = live.reduce((sum, r) => sum + r.picked, 0);
  return `Apply ${n.toLocaleString()} ${n === 1 ? "change" : "changes"}`;
}

/** The match list's header for a run of one rule. */
export function wouldText(rule: string, actions: RuleAction[], n: number): string {
  if (actions.length === 1) return `${rule} would ${lowerFirst(actionText(actions[0], n))}.`;
  return `${rule} would change ${mails(n)}: ${actions.map(describeAction).join(", ")}.`;
}

/** One line of the header for a run of several rules: "Receipts · label Receipts · 30". */
export function ruleLine(rule: string, actions: RuleAction[], n: number): string {
  return `${rule} · ${actions.map((a) => lowerFirst(describeAction(a))).join(", ")} · ${n.toLocaleString()}`;
}

/** How many picked mails would be offloaded or deleted forever, and by which actions. */
export function heavyKinds(rules: RulePick[]): { count: number; kinds: Set<string> } {
  const kinds = new Set<string>();
  let count = 0;
  for (const r of rules) {
    const heavy = r.actions.map((a) => a.kind).filter((k) => HEAVY_KINDS.includes(k));
    if (heavy.length === 0 || r.picked === 0) continue;
    count += r.picked;
    heavy.forEach((k) => kinds.add(k));
  }
  return { count, kinds };
}

/** The label of the typed-count field. */
export function confirmLabel(kinds: Set<string>, n: number): string {
  const what =
    kinds.has("offload") && kinds.has("delete_forever")
      ? "offload or delete forever these mails"
      : kinds.has("delete_forever")
        ? "delete these mails forever"
        : "offload these mails";
  return `Type ${n.toLocaleString()} to ${what}`;
}

/** What "Run it on older mail…" covers after Make a rule…: the one sender's mail anywhere in
 * Gmail; otherwise the list on screen as a pick-all; a list rules can't take (Suggested,
 * Offloaded, Deleted in Gmail, or All mail with no search or label) falls back to the Inbox. */
export function olderMailScope(senders: string[], view: string, label: string, q: string): RunScope {
  if (senders.length === 1) return { view: "all", q: `from:${senders[0]}` };
  if (viewKind(view) === "gmail" && (q.trim() !== "" || label !== "" || view !== "all")) return { view, ...(label ? { label } : {}), q, pick_all: true };
  return { inbox: true };
}
```

- [ ] **Step 4: Types, the match list query and the hooks**

In `frontend/src/api/rules.ts`:
- The first import becomes `import { useInfiniteQuery, useQuery } from "@tanstack/react-query";`.
- Replace `JobPreview` with:

```ts
export type JobPreview = {
  job_id: number;
  state: JobState;
  total: number;
  decided: number;
  planned: number;
  no_rule: number;
  applied: number;
  skipped: number;
  failed: number;
  reason: string | null;
  paused?: AiPause | null;
  per_rule: { rule: string; rule_id: number | null; count: number; actions: RuleAction[]; examples: { subject: string; sender: string; why: string }[] }[];
  /** The rules the run decides with ([]: every enabled rule, for runs from before 0.5.0). */
  rule_ids: number[];
  view: string;
  q: string;
  /** What it ran on, in words: "Inbox · from:shop". */
  filter_text: string;
  left_out: number;
  skip_reasons: { reason: string; count: number }[];
};
/** One row of a run's match list (running rules spec §5). */
export type RuleMatch = {
  id: number;
  message_id: number;
  thread_id: string;
  subject: string;
  from_addr: string;
  date: string;
  rule_id: number | null;
  rule_name: string;
  actions: RuleAction[];
  why: string;
};
/** What a run covers (running rules spec §8): the Inbox (a rule card's Run), a search or a
 * pick-all as the view, label and search, or threads picked by hand in a view. */
export type RunScope = { inbox: true } | { view: string; label?: string; q: string; pick_all?: true } | { view: string; label?: string; thread_ids: string[] };
export type RunStart = { rule_ids: number[]; scope: RunScope };
```

- Add after `useRuleJob`:

```ts
export const MATCH_PAGE = 50;
/** A run's match list, newest first, 50 a page; `ruleId` keeps one rule's matches. */
export const useRuleMatches = (accountId: number, jobId: number | null, ruleId: number | null) =>
  useInfiniteQuery({
    queryKey: ["rule-job-matches", jobId, ruleId],
    queryFn: ({ pageParam }) =>
      api<RuleMatch[]>(`/api/rule-jobs/${jobId}/matches${query({ account_id: accountId, rule_id: ruleId, before_id: pageParam, limit: MATCH_PAGE })}`),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => (last.length < MATCH_PAGE ? undefined : last[last.length - 1].id),
    enabled: jobId !== null,
  });
```

In `frontend/src/hooks/useRules.ts`:
- `RULE_KEYS` becomes `["rules", "rules-sorting", "rule-decisions", "rule-job", "rule-job-open", "rule-job-matches"]`.
- Add `RunStart` to the type import from `../api/rules`.
- Replace `useStartRuleJob` and `useRunRuleJob` with:

```ts
/** Starts a run (running rules spec §8): its rules, and what it covers. */
export const useStartRuleJob = () =>
  useChange((b: Acc & { start: RunStart }) =>
    post<{ job_id: number }>("/api/rule-jobs", { account_id: b.accountId, rule_ids: b.start.rule_ids, ...b.start.scope }),
  );

/** Approves a run's match list: `leftOut` are the unticked matches, or `only` the ticked ones
 * after Pick all was unticked. `confirm` is the typed count, for Offload and Delete forever. */
export const useRunRuleJob = () =>
  useChange((b: Acc & { jobId: number; leftOut?: number[]; only?: number[]; confirm?: number }) =>
    post<{ job_id: number }>(`/api/rule-jobs/${b.jobId}/run`, {
      account_id: b.accountId,
      left_out: b.leftOut ?? [],
      ...(b.only ? { only: b.only } : {}),
      ...(b.confirm !== undefined ? { confirm: b.confirm } : {}),
    }),
  );
```

In `frontend/src/components/mail/MailList.tsx`, `function PickAllBox(` becomes `export function PickAllBox(`.

- [ ] **Step 5: The Run rules window**

Replace `frontend/src/components/mail/RunRulesDialog.tsx` with:

```tsx
import { useEffect, useRef, useState } from "react";

import { friendlyError } from "../../api/client";
import { type JobPreview, type RuleMatch, type RunStart, useRuleJob, useRuleMatches } from "../../api/rules";
import type { Account } from "../../api/types";
import { useRunRuleJob, useStartRuleJob, useStopRuleJob } from "../../hooks/useRules";
import { formatDay } from "../../lib/format";
import { pauseLine } from "../../lib/pause";
import { approveText, confirmLabel, heavyKinds, ruleLine, wouldText } from "../../lib/ruleRuns";
import { Bar, Button, Dialog, Field, inputClass, Notice } from "../ui";
import { PickAllBox, type PickState } from "./MailList";
import { parseTyped } from "./OffloadMailDialog";

const pct = (done: number, of: number) => (of > 0 ? (done / of) * 100 : 0);

/** The match list's picks (running rules spec §5). "all": every match except `ids`, the ones
 * left out (what the server stores). "only": just `ids`, after Pick all was unticked. Each id
 * keeps its rule, so the counts per rule are known without loading every page. */
type Picks = { mode: "all" | "only"; ids: Map<number, number | null> };
const everyMatch = (): Picks => ({ mode: "all", ids: new Map() });
const isPicked = (p: Picks, id: number) => (p.mode === "all" ? !p.ids.has(id) : p.ids.has(id));

/** How many of a rule's `count` matches are picked. */
function pickedOf(p: Picks, ruleId: number | null, count: number): number {
  let mine = 0;
  for (const r of p.ids.values()) if (r === ruleId) mine += 1;
  return p.mode === "all" ? count - mine : mine;
}

/** What the run would do: a sentence for one rule, a line per rule for several. */
function MatchHeader({ job }: { job: JobPreview }) {
  const noFit = `${job.no_rule.toLocaleString()} didn't fit.`;
  const [first] = job.per_rule;
  return (
    <div className="space-y-1">
      {job.filter_text && <p className="text-table text-ink-2">On: {job.filter_text}</p>}
      {job.per_rule.length === 1 ? (
        <p>{`${wouldText(first.rule, first.actions, first.count)} ${noFit}`}</p>
      ) : (
        <>
          <ul aria-label="What each rule would do" className="text-table">
            {job.per_rule.map((r) => (
              <li key={`${r.rule_id ?? ""}-${r.rule}`}>{ruleLine(r.rule, r.actions, r.count)}</li>
            ))}
          </ul>
          <p className="text-table text-ink-2">{noFit}</p>
        </>
      )}
    </div>
  );
}

function Matches({
  accountId,
  jobId,
  ruleId,
  several,
  picks,
  onToggle,
}: {
  accountId: number;
  jobId: number;
  ruleId: number | null;
  several: boolean;
  picks: Picks;
  onToggle: (m: RuleMatch) => void;
}) {
  const found = useRuleMatches(accountId, jobId, ruleId);
  const rows = found.data?.pages.flat() ?? [];
  return (
    <>
      {found.isPending && <p className="px-4 py-2 text-ink-2">Loading…</p>}
      {found.isError && <Notice tone="err">{friendlyError(found.error)}</Notice>}
      <ul aria-label="Matched mail" className="max-h-[45vh] overflow-y-auto">
        {rows.map((m) => (
          <li key={m.id} className="flex gap-3 border-b border-line px-4 py-2 last:border-b-0">
            <input
              type="checkbox"
              className="mt-1 h-4 w-4 shrink-0 accent-accent"
              aria-label={`Pick ${m.subject || "(no subject)"}`}
              checked={isPicked(picks, m.id)}
              onChange={() => onToggle(m)}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate font-semibold">{m.from_addr}</span>
                <span className="shrink-0 text-meta text-ink-2 tabular">{formatDay(m.date)}</span>
              </div>
              <span className="block truncate">{m.subject || "(no subject)"}</span>
              <span className="block text-meta text-ink-2">
                {several && <span className="mr-2 rounded-full bg-sunken px-2 py-0.5 font-semibold text-ink">{m.rule_name}</span>}
                {m.why}
              </span>
            </div>
          </li>
        ))}
      </ul>
      {found.hasNextPage && (
        <div className="px-4 py-2">
          <Button disabled={found.isFetchingNextPage} onClick={() => void found.fetchNextPage()}>
            Show more
          </Button>
        </div>
      )}
    </>
  );
}

type Props = { account: Account; onClose: () => void } & ({ start: RunStart; jobId?: undefined } | { jobId: number; start?: undefined });

/** Running rules (running rules spec §5): the run's rules decide every mail first (nothing
 * changes in Gmail), then the match list shows what they matched, every mail picked, and the
 * approve button makes the changes for the picked ones. With `jobId` it shows a run that is
 * already there instead of starting one. */
export function RunRulesDialog({ account, start, jobId: existing, onClose }: Props) {
  const begin = useStartRuleJob();
  const runJob = useRunRuleJob();
  const stop = useStopRuleJob();
  const [jobId, setJobId] = useState<number | null>(existing ?? null);
  const [typed, setTyped] = useState("");
  const [picks, setPicks] = useState<Picks>(everyMatch);
  const [shown, setShown] = useState<number | null>(null); // the rule filter; null: every rule
  const asked = useRef(false); // one job per opening, even when an effect runs twice
  const closed = useRef(false); // closed before the job had started: stop it once it has an id
  useEffect(() => {
    if (asked.current || !start) return;
    asked.current = true;
    begin
      .mutateAsync({ accountId: account.id, start })
      .then(({ job_id }) => {
        if (closed.current) stop.mutate({ accountId: account.id, jobId: job_id });
        else setJobId(job_id);
      })
      .catch(() => undefined); // the hook shows the error
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the dialog opens
  }, []);
  const job = useRuleJob(account.id, jobId).data;
  const state = job?.state;
  const deciding = !begin.isError && (!job || state === "PREVIEWING");
  const planned = job?.planned ?? 0;
  const listing = job !== undefined && state === "PREVIEWED" && planned > 0;
  const perRule = (job?.per_rule ?? []).map((r) => ({ ...r, picked: pickedOf(picks, r.rule_id, r.count) }));
  const pickedTotal = picks.mode === "all" ? planned - picks.ids.size : picks.ids.size;
  const pickState: PickState = picks.mode === "all" && picks.ids.size === 0 ? "all" : pickedTotal > 0 ? "some" : "none";
  const heavy = heavyKinds(perRule);
  const ok = pickedTotal > 0 && (heavy.count === 0 || parseTyped(typed) === heavy.count);
  const finished = state === "DONE" || state === "STOPPED";
  const changed = job ? job.applied + job.skipped + job.failed : 0;
  const stopJob = () => jobId !== null && stop.mutate({ accountId: account.id, jobId });
  const cancel = () => {
    closed.current = true;
    // Only a run that is making changes keeps going: a list left open would block the next run.
    if (state === "PREVIEWING" || state === "PREVIEWED" || (jobId !== null && !job && existing === undefined)) stopJob();
    onClose();
  };
  const toggle = (m: RuleMatch) => {
    const ids = new Map(picks.ids);
    if (ids.has(m.id)) ids.delete(m.id);
    else ids.set(m.id, m.rule_id);
    setPicks({ mode: picks.mode, ids });
  };
  const togglePickAll = () => setPicks(pickState === "all" ? { mode: "only", ids: new Map() } : everyMatch());
  const approve = () =>
    runJob.mutate(
      {
        accountId: account.id,
        jobId: job!.job_id,
        ...(picks.mode === "all" ? { leftOut: [...picks.ids.keys()] } : { only: [...picks.ids.keys()] }),
        ...(heavy.count > 0 ? { confirm: heavy.count } : {}),
      },
      { onError: () => setTyped("") },
    );
  return (
    <Dialog
      title="Run rules"
      wide
      onClose={cancel}
      footer={
        <>
          {state === "RUNNING" && (
            <Button tone="danger" disabled={stop.isPending} onClick={stopJob}>
              Stop
            </Button>
          )}
          <Button onClick={cancel}>{deciding || listing ? "Cancel" : "Close"}</Button>
          {listing && (
            <Button tone={heavy.count > 0 ? "danger" : "primary"} disabled={!ok || runJob.isPending} onClick={approve}>
              {approveText(perRule)}
            </Button>
          )}
        </>
      }
    >
      {begin.isError && <Notice tone="err">The rules couldn't start. Nothing was changed.</Notice>}
      {deciding && (
        <div className="space-y-2">
          <p>{job ? `Deciding… ${job.decided.toLocaleString()} of ${job.total.toLocaleString()}` : "Deciding…"}</p>
          <Bar value={job ? pct(job.decided, job.total) : 0} label="Deciding" />
          {job?.paused && <p className="text-table text-ink-2">{pauseLine(job.paused)}</p>}
        </div>
      )}
      {job && state === "PREVIEWED" && planned === 0 && <p>{job.rule_ids.length === 1 ? "No mail fits this rule." : "No mail fits these rules."}</p>}
      {job && listing && (
        <>
          <MatchHeader job={job} />
          {job.per_rule.length > 1 && (
            <Field label="Show">
              <select className={inputClass} value={shown ?? ""} onChange={(e) => setShown(e.target.value === "" ? null : Number(e.target.value))}>
                <option value="">Every rule</option>
                {job.per_rule.map((r) =>
                  r.rule_id === null ? null : (
                    <option key={r.rule_id} value={r.rule_id}>
                      {r.rule}
                    </option>
                  ),
                )}
              </select>
            </Field>
          )}
          <div className="rounded-card border border-line">
            <PickAllBox state={pickState} onChange={togglePickAll} />
            <Matches accountId={account.id} jobId={job.job_id} ruleId={shown} several={job.per_rule.length > 1} picks={picks} onToggle={toggle} />
          </div>
          {heavy.count > 0 && (
            <Field label={confirmLabel(heavy.kinds, heavy.count)}>
              <input className={`${inputClass} w-40`} inputMode="numeric" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
            </Field>
          )}
        </>
      )}
      {job && state === "RUNNING" && (
        <div className="space-y-2">
          <p>
            Changing… {changed.toLocaleString()} of {(changed + planned).toLocaleString()}
          </p>
          <Bar value={pct(changed, changed + planned)} label="Changing" tone="run" />
          <p className="text-table text-ink-2">It keeps going if you close this.</p>
        </div>
      )}
      {job && finished && (
        <div className="space-y-2">
          <p>
            {`${state === "STOPPED" ? "Stopped." : "Done."} Applied ${job.applied.toLocaleString()}, skipped ${job.skipped.toLocaleString()}, failed ${job.failed.toLocaleString()}, left out ${job.left_out.toLocaleString()}.`}
          </p>
          {job.reason && <p className="text-table text-ink-2">{job.reason}</p>}
          {job.skip_reasons.length > 0 && (
            <ul aria-label="Why mail was skipped" className="list-disc space-y-1 pl-5 text-table text-ink-2">
              {job.skip_reasons.map((r) => (
                <li key={r.reason}>{`${r.reason} (${r.count.toLocaleString()})`}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {job && state === "FAILED" && <Notice tone="err">{job.reason ?? "The rules stopped. Try again."}</Notice>}
    </Dialog>
  );
}
```

- [ ] **Step 6: Make a rule… runs only the saved rule; "Run rules on…" goes**

In `frontend/src/components/mail/MakeRuleDialog.tsx`:
- The imports become:

```tsx
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { friendlyError } from "../../api/client";
import type { Rule, RunStart } from "../../api/rules";
import type { Account } from "../../api/types";
import { useDraftFromMail } from "../../hooks/useRules";
import { mailView } from "../../lib/mail";
import { olderMailScope } from "../../lib/ruleRuns";
import { RuleForm } from "../rules/RuleForm";
import { Button, Dialog, Notice } from "../ui";
import { RunRulesDialog } from "./RunRulesDialog";
```

- Delete the `labelName` line, and `const [run, setRun] = useState<RunRulesStart | null>(null);` becomes `const [run, setRun] = useState<RunStart | null>(null);`.
- In the `if (saved)` block, replace everything from `const senders = …` to the end of `const start: RunRulesStart = …;` with:

```tsx
    const scope = olderMailScope(draft.data?.conditions.senders_in ?? [], mailView(params.get("view")), label, params.get("q") ?? "");
```

  and the button's `onClick={() => setRun(start)}` becomes `onClick={() => setRun({ rule_ids: [saved.id], scope })}`.

In `frontend/src/components/mail/MailActions.tsx`, `MailHeaderActions` loses "Run rules on…". Remove the imports of `runRulesStart` and of `RunRulesDialog, type RunRulesStart`, so the `lib/mail` import is `import { viewKind } from "../../lib/mail";`. Its destructuring becomes `{ account, q, view, scope = null }` (the prop type keeps `label?: string`, which callers still pass and Task 6 uses again; `noUnusedParameters` would refuse an unused `label` binding). Replace the function's body with:

```tsx
  const [open, setOpen] = useState(false);
  if (viewKind(view) !== "gmail") return null;
  return (
    <>
      <Button tone="main" onClick={() => setOpen(true)}>
        Offload mail…
      </Button>
      {open && <OffloadMailDialog account={account} start={{ q }} scope={scope} onClose={() => setOpen(false)} />}
    </>
  );
```

In `frontend/src/pages/MailRulesPage.tsx`, in `Footer`: delete `<Button onClick={() => setOpen("run")}>Run rules on…</Button>` and the line `{open === "run" && <RunRulesDialog … />}`, and the state becomes `useState<"starters" | "copy" | null>(null)`.

In `frontend/src/lib/mail.ts`, delete `RUN_RULES_VIEWS`, `runRulesStart` and its doc comment. Delete `quoted` too, unless `grep -n "quoted(" src/lib/mail.ts` still finds another use.

- [ ] **Step 7: Run the frontend checks**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck && grep -rn "Run rules on…\|runRulesStart\|RunRulesStart" src`
Expected: tests, lint and typecheck PASS, and the grep finds nothing.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/ruleRuns.ts frontend/src/lib/ruleRuns.test.ts frontend/src/api/rules.ts frontend/src/hooks/useRules.ts frontend/src/components/mail/MailList.tsx frontend/src/components/mail/RunRulesDialog.tsx frontend/src/components/mail/MakeRuleDialog.tsx frontend/src/components/mail/MailActions.tsx frontend/src/pages/MailRulesPage.tsx frontend/src/lib/mail.ts frontend/src/components/mail/MailAi.test.tsx
git commit -m "feat(rules): approve a run from the list of matched mails; Run rules on… goes

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Run on a rule card

**Files:**
- Modify: `frontend/src/lib/ruleRuns.ts` (`runBlock`), `frontend/src/lib/ruleRuns.test.ts`
- Modify: `frontend/src/components/rules/RuleList.tsx` (`RunButton`, props `modelSet`, `onRun`)
- Modify: `frontend/src/pages/MailRulesPage.tsx` (opens the run)
- Modify: `frontend/src/components/rules/Rules.test.tsx`

**Interfaces:**
- Consumes: Task 4's `RunRulesDialog` with `start: RunStart`; `Rule` from `api/rules.ts`; `useSorting(...).data.model_set`.
- Produces:
  - `runBlock(rule: { enabled: boolean; combine: string }, modelSet: boolean): string | null` in `lib/ruleRuns.ts`
  - `RuleList({ rules, accountId, modelSet, onRun }: { rules: Rule[]; accountId: number; modelSet: boolean; onRun: (rule: Rule) => void })`
  - Each card's button with accessible name `Run, <rule name>`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/lib/ruleRuns.test.ts`, add `runBlock` to the import and add:

```ts
  it("says why a rule can't run", () => {
    expect(runBlock({ enabled: false, combine: "fixed_only" }, true)).toBe("Turn the rule on to run it.");
    expect(runBlock({ enabled: true, combine: "ai_only" }, false)).toBe("Set up a model in Settings → Models.");
    expect(runBlock({ enabled: true, combine: "fixed_then_ai" }, false)).toBe("Set up a model in Settings → Models.");
    expect(runBlock({ enabled: true, combine: "fixed_only" }, false)).toBeNull();
    expect(runBlock({ enabled: true, combine: "ai_only" }, true)).toBeNull();
  });
```

In `frontend/src/components/rules/Rules.test.tsx`, add inside the describe:

```tsx
  it("runs a rule from its card over the Inbox, and has no Run rules on… any more", async () => {
    const job = { job_id: 9, state: "PREVIEWING", total: 12, decided: 3, planned: 0, no_rule: 0, applied: 0, skipped: 0, failed: 0, reason: null, per_rule: [], view: "inbox", q: "", rule_ids: [1], filter_text: "Inbox", left_out: 0, skip_reasons: [] };
    const { calls } = mockApi({ ...ACCOUNTS, "GET /api/rules": [RULE], "GET /api/rules/sorting": SORTING, "GET /api/rule-jobs/open": null, "POST /api/rule-jobs": { job_id: 9 }, "GET /api/rule-jobs/9": job });
    renderApp(<MailRulesPage />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: "Run, Receipts" }));
    expect(await screen.findByText("Deciding… 3 of 12")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/rule-jobs")?.body).toEqual({ account_id: 1, rule_ids: [1], inbox: true });
    expect(screen.queryByRole("button", { name: "Run rules on…" })).not.toBeInTheDocument();
  });

  it("disables Run, with the reason, for a rule that is off or needs a model that isn't set", async () => {
    const OFF = { ...RULE, id: 2, name: "Old news", enabled: false, position: 1 };
    const FIXED = { ...RULE, id: 3, name: "Calendar", combine: "fixed_only", conditions: { calendar_invite: true }, position: 2 };
    mockApi({ ...ACCOUNTS, "GET /api/rules": [RULE, OFF, FIXED], "GET /api/rules/sorting": { ...SORTING, model_set: false } });
    renderApp(<MailRulesPage />, { withAccount: true });
    const receipts = await screen.findByRole("button", { name: "Run, Receipts" });
    expect(receipts).toBeDisabled();
    expect(receipts).toHaveAccessibleDescription("Set up a model in Settings → Models.");
    const off = screen.getByRole("button", { name: "Run, Old news" });
    expect(off).toBeDisabled();
    expect(off).toHaveAccessibleDescription("Turn the rule on to run it.");
    expect(screen.getByRole("button", { name: "Run, Calendar" })).toBeEnabled();
  });
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd frontend && npm test -- --run src/lib/ruleRuns.test.ts src/components/rules/Rules.test.tsx`
Expected: FAIL. `runBlock` isn't exported and the cards have no Run button.

- [ ] **Step 3: `runBlock`**

Add to `frontend/src/lib/ruleRuns.ts`:

```ts
/** Why a rule can't run now (running rules spec §3), or null. Any rule that isn't
 * fixed-conditions-only may ask the model. */
export function runBlock(rule: { enabled: boolean; combine: string }, modelSet: boolean): string | null {
  if (!rule.enabled) return "Turn the rule on to run it.";
  if (rule.combine !== "fixed_only" && !modelSet) return "Set up a model in Settings → Models.";
  return null;
}
```

- [ ] **Step 4: The card's Run**

In `frontend/src/components/rules/RuleList.tsx`:
- Add `import { runBlock } from "../../lib/ruleRuns";`.
- Add above `RuleList`:

```tsx
/** Run on a rule card (running rules spec §3): this rule alone over the Inbox. Disabled, with
 * the reason beside it, when the rule is off or needs a model that isn't set up. */
function RunButton({ rule, modelSet, onRun }: { rule: Rule; modelSet: boolean; onRun: (rule: Rule) => void }) {
  const blocked = runBlock(rule, modelSet);
  const why = `run-why-${rule.id}`;
  return (
    <>
      <Button className="h-8" disabled={blocked !== null} aria-label={`Run, ${rule.name}`} aria-describedby={blocked ? why : undefined} onClick={() => onRun(rule)}>
        Run
      </Button>
      {blocked && (
        <span id={why} className="self-center text-meta text-ink-2">
          {blocked}
        </span>
      )}
    </>
  );
}
```

- `RuleList`'s signature becomes:

```tsx
/** The rules in the order they are asked, as cards. Drag a card, or use Move up / Move down.
 * `onRun` opens a run of one rule over the Inbox; `modelSet` says whether Sorting has a model. */
export function RuleList({ rules, accountId, modelSet, onRun }: { rules: Rule[]; accountId: number; modelSet: boolean; onRun: (rule: Rule) => void }) {
```

- In the card's button row, right after the Edit button, add `<RunButton rule={rule} modelSet={modelSet} onRun={onRun} />`.

In `frontend/src/pages/MailRulesPage.tsx`:
- The rules import becomes `import { describeAction, type Rule, rulesExportUrl, type Sorting, useOpenRuleJob, useRules, useSorting, useStarters } from "../api/rules";`.
- In `MailRulesPage`, add `const [running, setRunning] = useState<Rule | null>(null);` after `const sorting = …` (before the `if (!account)` return, so the hooks keep their order).
- The `RuleList` line becomes `{rules.data && <RuleList key={`list-${account.id}`} rules={rules.data} accountId={account.id} modelSet={sorting?.model_set ?? true} onRun={setRunning} />}`.
- Add after it:

```tsx
      {running && <RunRulesDialog key={running.id} account={account} start={{ rule_ids: [running.id], scope: { inbox: true } }} onClose={() => setRunning(null)} />}
```

- [ ] **Step 5: Run the frontend checks**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/ruleRuns.ts frontend/src/lib/ruleRuns.test.ts frontend/src/components/rules/RuleList.tsx frontend/src/pages/MailRulesPage.tsx frontend/src/components/rules/Rules.test.tsx
git commit -m "feat(rules): Run on a rule card decides that rule over the Inbox

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Run rules on a search or a pick

**Files:**
- Create: `frontend/src/components/mail/RulePickerDialog.tsx`
- Modify: `frontend/src/lib/ruleRuns.ts` (`runOnText`), `frontend/src/lib/ruleRuns.test.ts`
- Modify: `frontend/src/components/mail/MailActions.tsx` (`MailHeaderActions`: Run rules above a search)
- Modify: `frontend/src/components/mail/MailReader.tsx` (Run rules on the picked-mail bar; header props)
- Modify: `frontend/src/components/mail/MailReader.test.tsx`

**Interfaces:**
- Consumes:
  - Task 4's `RunRulesDialog`, `RunScope`; Task 5's `runBlock`; `useRules`, `useSorting`, `describeAction` from `api/rules.ts`.
  - Round 1's `MailReader` internals: `type PickedAction`, `pickedAction`/`setPickedAction`, `all`, `picked`, `pickAll: MailPickAll`, `kind`, `scope`, `totals`, `pickState`, and the bar's `kind === "gmail"` buttons. Round 1's test helpers in `MailReader.test.tsx`: `thread(...)`, `ACTIVE`.
- Produces:
  - `RulePickerDialog({ account, scope, onText, onClose }: { account: Account; scope: RunScope; onText: string; onClose: () => void })`
  - `runOnText(on: { threads: number } | { total: number | undefined; q: string; place: string }): string`
  - `MailHeaderActions({ account, q, view, label, scope, total, picking })` with `total?: number` and `picking?: boolean`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/lib/ruleRuns.test.ts`, add `runOnText` to the import and add:

```ts
  it("says what Run rules will run on", () => {
    expect(runOnText({ total: 1284, q: "from:shop.example.com", place: "Inbox" })).toBe("Run on: 1,284 mails matching from:shop.example.com");
    expect(runOnText({ total: 1204, q: "", place: "Inbox" })).toBe("Run on: 1,204 mails in Inbox");
    expect(runOnText({ total: undefined, q: "x", place: "All mail" })).toBe("Run on: the mails matching x");
    expect(runOnText({ threads: 1 })).toBe("Run on: 1 picked thread");
    expect(runOnText({ threads: 37 })).toBe("Run on: 37 picked threads");
  });
```

In `frontend/src/components/mail/MailReader.test.tsx`, add `type Call` to the `../../test/utils` import, and add at the end:

```tsx
describe("Run rules on a search or a pick", () => {
  const TWO = { threads: [thread({ thread_id: "t1", subject: "One" }), thread({ thread_id: "t2", subject: "Two" })], next_cursor: null, total: null, total_bytes: null };
  const base = { description: "x", mode: "suggest", position: 0, version: 1, decided_7d: 0, senders: [] };
  const RULES = [
    { ...base, id: 2, name: "Marketing", conditions: { list_unsubscribe: true }, combine: "fixed_then_ai", actions: [{ kind: "label", label: "Marketing" }, { kind: "archive" }], enabled: true },
    { ...base, id: 3, name: "Calendar", conditions: { calendar_invite: true }, combine: "fixed_only", actions: [{ kind: "label", label: "Calendar" }], enabled: true, position: 1 },
    { ...base, id: 4, name: "Old", conditions: {}, combine: "ai_only", actions: [{ kind: "archive" }], enabled: false, position: 2 },
  ];
  const JOB = { job_id: 9, state: "PREVIEWING", total: 2, decided: 0, planned: 0, no_rule: 0, applied: 0, skipped: 0, failed: 0, reason: null, per_rule: [], view: "inbox", q: "", rule_ids: [2], filter_text: "", left_out: 0, skip_reasons: [] };
  const api = (modelSet = true) =>
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": TWO,
      "GET /api/mail/totals": { total: 1284, total_bytes: 1 },
      "GET /api/rules": RULES,
      "GET /api/rules/sorting": { enabled: false, waiting: 0, sorted_today: 0, suggested: 0, failing_since: null, last_error: null, warning: false, model_set: modelSet, paused: null },
      "POST /api/rule-jobs": { job_id: 9 },
      "GET /api/rule-jobs/9": JOB,
    });
  const open = (route: string) => renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route });
  const started = (calls: Call[]) => calls.find((c) => c.method === "POST" && c.path === "/api/rule-jobs")?.body;

  it("is offered above a search in a Gmail view, not on a plain view or in CleanSync's views", async () => {
    api();
    const plain = open("/mail?view=inbox");
    await screen.findByRole("checkbox", { name: "Pick One" });
    expect(screen.queryByRole("button", { name: "Run rules" })).not.toBeInTheDocument();
    plain.unmount();
    for (const route of ["/mail?view=offloaded&q=shop", "/mail?view=deleted&q=shop", "/mail?view=suggested&q=shop"]) {
      const r = open(route);
      await userEvent.click(await screen.findByRole("checkbox", { name: "Pick One" }));
      expect(screen.queryByRole("button", { name: "Run rules" }), route).not.toBeInTheDocument();
      r.unmount();
    }
    open("/mail?view=inbox&q=from%3Ashop");
    expect(await screen.findByRole("button", { name: "Run rules" })).toBeInTheDocument();
  });

  it("runs the ticked rules on every mail of the search", async () => {
    const { calls } = api();
    open("/mail?view=inbox&q=from%3Ashop");
    await screen.findByText(/1,284 mails/);
    await userEvent.click(await screen.findByRole("button", { name: "Run rules" }));
    const dialog = screen.getByRole("dialog", { name: "Run rules" });
    expect(within(dialog).getByText("Run on: 1,284 mails matching from:shop")).toBeInTheDocument();
    const run = within(dialog).getByRole("button", { name: "Run" });
    expect(run).toBeDisabled();
    expect(await within(dialog).findByRole("checkbox", { name: /^Marketing/ })).not.toBeChecked();
    expect(within(dialog).queryByRole("checkbox", { name: /^Old/ })).not.toBeInTheDocument(); // turned off
    await userEvent.click(within(dialog).getByRole("checkbox", { name: /^Marketing/ }));
    await userEvent.click(run);
    expect(await screen.findByText("Deciding… 0 of 2")).toBeInTheDocument();
    expect(started(calls)).toEqual({ account_id: 1, rule_ids: [2], view: "inbox", q: "from:shop" });
  });

  it("runs on picked threads, or on a pick-all as the view, label and search", async () => {
    const { calls } = api();
    const first = open("/mail?view=inbox");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick One" }));
    await userEvent.click(within(screen.getByRole("region", { name: "Picked mail" })).getByRole("button", { name: "Run rules" }));
    let dialog = screen.getByRole("dialog", { name: "Run rules" });
    expect(within(dialog).getByText("Run on: 1 picked thread")).toBeInTheDocument();
    await userEvent.click(await within(dialog).findByRole("checkbox", { name: /^Calendar/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Run" }));
    await waitFor(() => expect(started(calls)).toEqual({ account_id: 1, rule_ids: [3], view: "inbox", thread_ids: ["t1"] }));
    first.unmount();
    const again = api();
    open("/mail?label=Label_2&q=older_than%3A1y");
    await screen.findByText(/1,284 mails/);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick all" }));
    expect(screen.getAllByRole("button", { name: "Run rules" })).toHaveLength(1); // the bar's only
    await userEvent.click(within(screen.getByRole("region", { name: "Picked mail" })).getByRole("button", { name: "Run rules" }));
    dialog = screen.getByRole("dialog", { name: "Run rules" });
    expect(within(dialog).getByText("Run on: 1,284 mails matching older_than:1y")).toBeInTheDocument();
    await userEvent.click(await within(dialog).findByRole("checkbox", { name: /^Marketing/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Run" }));
    await waitFor(() => expect(started(again.calls)).toEqual({ account_id: 1, rule_ids: [2], view: "all", label: "Label_2", q: "older_than:1y", pick_all: true }));
  });

  it("greys out a rule that needs a model when none is set", async () => {
    api(false);
    open("/mail?view=inbox&q=x");
    await userEvent.click(await screen.findByRole("button", { name: "Run rules" }));
    const dialog = screen.getByRole("dialog", { name: "Run rules" });
    expect(await within(dialog).findByRole("checkbox", { name: /^Marketing/ })).toBeDisabled();
    expect(within(dialog).getByText("Set up a model in Settings → Models.")).toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox", { name: /^Calendar/ })).toBeEnabled();
  });
});
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd frontend && npm test -- --run src/lib/ruleRuns.test.ts src/components/mail/MailReader.test.tsx`
Expected: FAIL. `runOnText` isn't exported and there is no Run rules button.

- [ ] **Step 3: `runOnText`**

Add to `frontend/src/lib/ruleRuns.ts`:

```ts
/** The rule picker's first line (running rules spec §4): what the ticked rules will run on. */
export function runOnText(on: { threads: number } | { total: number | undefined; q: string; place: string }): string {
  if ("threads" in on) return `Run on: ${on.threads.toLocaleString()} picked ${on.threads === 1 ? "thread" : "threads"}`;
  const n = on.total === undefined ? "the mails" : mails(on.total);
  return on.q.trim() ? `Run on: ${n} matching ${on.q.trim()}` : `Run on: ${n} in ${on.place}`;
}
```

- [ ] **Step 4: The rule picker**

```tsx
// frontend/src/components/mail/RulePickerDialog.tsx
import { useState } from "react";

import { friendlyError } from "../../api/client";
import { describeAction, type RunScope, useRules, useSorting } from "../../api/rules";
import type { Account } from "../../api/types";
import { runBlock } from "../../lib/ruleRuns";
import { Button, Dialog, Notice } from "../ui";
import { RunRulesDialog } from "./RunRulesDialog";

/** Run rules on a search or a pick (running rules spec §4): tick the rules to run first. Only
 * rules that are on are listed, in their order; each mail gets the first ticked rule that fits.
 * `onText` says what they will run on. */
export function RulePickerDialog({ account, scope, onText, onClose }: { account: Account; scope: RunScope; onText: string; onClose: () => void }) {
  const rules = useRules(account.id);
  const modelSet = useSorting(account.id).data?.model_set ?? true;
  const [ticked, setTicked] = useState<Set<number>>(new Set());
  const [run, setRun] = useState<number[] | null>(null);
  if (run) return <RunRulesDialog account={account} start={{ rule_ids: run, scope }} onClose={onClose} />;
  const on = (rules.data ?? []).filter((r) => r.enabled);
  const toggle = (id: number) => {
    const next = new Set(ticked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setTicked(next);
  };
  return (
    <Dialog
      title="Run rules"
      wide
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button tone="primary" disabled={ticked.size === 0} onClick={() => setRun(on.filter((r) => ticked.has(r.id)).map((r) => r.id))}>
            Run
          </Button>
        </>
      }
    >
      <p>{onText}</p>
      {rules.isPending && <p className="text-ink-2">Loading rules…</p>}
      {rules.isError && <Notice tone="err">{friendlyError(rules.error)}</Notice>}
      {rules.data && on.length === 0 && <p className="text-ink-2">This mailbox has no rules that are on. Add one in Mail → Rules.</p>}
      <ul aria-label="Rules to run" className="max-h-[50vh] space-y-2 overflow-y-auto">
        {on.map((r) => {
          const why = runBlock(r, modelSet);
          return (
            <li key={r.id}>
              <label className="flex gap-2">
                <input type="checkbox" className="mt-1" disabled={why !== null} checked={ticked.has(r.id)} onChange={() => toggle(r.id)} />
                <span>
                  <span className="block font-semibold">{r.name}</span>
                  <span className="block text-table">{r.actions.map(describeAction).join(", ")}</span>
                  {why && <span className="block text-meta text-ink-2">{why}</span>}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}
```

- [ ] **Step 5: Run rules above a search, and on the picked-mail bar**

In `frontend/src/components/mail/MailActions.tsx`, add `import { runOnText } from "../../lib/ruleRuns";` and `import { RulePickerDialog } from "./RulePickerDialog";`, and replace `MailHeaderActions` with:

```tsx
/** The page's main action (spec §9: the deep indigo button), in the Gmail views only, and Run
 * rules above a search when nothing is picked (running rules spec §4; picked mail has Run rules
 * on its bar). `scope` names the view or label on screen; `total` is the search's mail count. */
export function MailHeaderActions({
  account,
  q,
  view,
  label = "",
  scope = null,
  total,
  picking = false,
}: {
  account: Account;
  q: string;
  view: string;
  label?: string;
  scope?: string | null;
  total?: number;
  picking?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [rules, setRules] = useState(false);
  if (viewKind(view) !== "gmail") return null;
  return (
    <>
      {q.trim() !== "" && !picking && <Button onClick={() => setRules(true)}>Run rules</Button>}
      <Button tone="main" onClick={() => setOpen(true)}>
        Offload mail…
      </Button>
      {open && <OffloadMailDialog account={account} start={{ q }} scope={scope} onClose={() => setOpen(false)} />}
      {rules && (
        <RulePickerDialog
          account={account}
          scope={{ view, ...(label ? { label } : {}), q }}
          onText={runOnText({ total, q, place: scope ?? "All mail" })}
          onClose={() => setRules(false)}
        />
      )}
    </>
  );
}
```

In `frontend/src/components/mail/MailReader.tsx`:
- Add `import { runOnText } from "../../lib/ruleRuns";` and `import { RulePickerDialog } from "./RulePickerDialog";`.
- `type PickedAction = "offload" | "rule" | null;` becomes `type PickedAction = "offload" | "rule" | "rules" | null;`.
- The header call becomes `<MailHeaderActions account={account} q={q} view={view} label={label} scope={scope} total={totals?.total} picking={pickState !== "none"} />`.
- In the picked-mail bar's `kind === "gmail"` fragment, add after the Make a rule… button: `<Button onClick={() => setPickedAction("rules")}>Run rules</Button>`.
- After `{pickedAction === "rule" && <MakeRuleDialog … />}`, add:

```tsx
      {pickedAction === "rules" && (
        <RulePickerDialog
          account={account}
          scope={all ? { ...pickAll, pick_all: true } : { view, ...(label ? { label } : {}), thread_ids: [...picked] }}
          onText={all ? runOnText({ total: totals?.total, q, place: scope ?? "All mail" }) : runOnText({ threads: picked.size })}
          onClose={() => setPickedAction(null)}
        />
      )}
```

- [ ] **Step 6: Run the frontend checks**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/mail/RulePickerDialog.tsx frontend/src/lib/ruleRuns.ts frontend/src/lib/ruleRuns.test.ts frontend/src/components/mail/MailActions.tsx frontend/src/components/mail/MailReader.tsx frontend/src/components/mail/MailReader.test.tsx
git commit -m "feat(rules): Run rules on a search or a pick, with the rules ticked first

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Marketing mail in the sample mailbox, the browser test, and the docs

**Files:**
- Modify: `backend/app/devtools/fake_gmail.py` (two marketing mails; `SAMPLE_KEPT`, `SAMPLE_TOTAL`)
- Modify: `backend/tests/test_rules_running.py`
- Modify: `frontend/e2e/helpers.ts` (`setUpMail` counts), `frontend/e2e/smoke.spec.ts` (counts)
- Modify: `frontend/e2e/rules.spec.ts` (step 3 of the first test; a new test)
- Modify: `docs/mail-rules.md` (§4 and the line pointing to it), `docs/architecture.md`, `docs/limitations.md`

**Interfaces:**
- Consumes: everything above through the real app; round 1's `/mail?view=archived`, the `rows(page, view)` helper in `e2e/rules.spec.ts`, and round 1's steps 5–7 of the first rules test.
- Produces: `SAMPLE_KEPT = 26`, `SAMPLE_TOTAL = 28`; mails "Big sale: 30% off shoes" and "Spring offers just for you" from `deals@shop.example.com` with `List-Unsubscribe`, in the Inbox.

- [ ] **Step 1: Write the failing backend test**

Add to `backend/tests/test_rules_running.py`:

```python
def test_the_sample_mailbox_has_mail_the_marketing_starter_fits():
    import email
    from datetime import UTC, datetime

    from app.devtools import fake_model
    from app.devtools.fake_gmail import SAMPLE_TOTAL, FakeGmail, sample_mailbox
    from app.services.rules.store import STARTERS

    fake = FakeGmail("alice@example.com")
    sample_mailbox(fake, datetime(2026, 10, 13, tzinfo=UTC))
    assert len(fake.messages) == SAMPLE_TOTAL == 28
    heads = [email.message_from_bytes(m.raw) for m in fake.messages.values()]
    lists = sorted(str(h["Subject"]) for h in heads if h["List-Unsubscribe"])
    assert lists == ["Big sale: 30% off shoes", "Spring offers just for you"]
    marketing = STARTERS["Marketing"].description
    for subject in lists:  # the pretend model picks Marketing for both, as the browser test needs
        asked = f"1. Marketing: {marketing}\n<<<EMAIL\nSubject: {subject}\n"
        assert fake_model._choose(asked)["rule"] == 1
```

Run: `cd backend && uv run pytest tests/test_rules_running.py::test_the_sample_mailbox_has_mail_the_marketing_starter_fits -q`
Expected: FAIL (`SAMPLE_TOTAL` is 26, and no sample mail has `List-Unsubscribe`).

- [ ] **Step 2: The marketing mails**

In `backend/app/devtools/fake_gmail.py`, `SAMPLE_KEPT = 24` becomes `SAMPLE_KEPT = 26` and `SAMPLE_TOTAL = 26  # …` becomes `SAMPLE_TOTAL = 28  # with one Spam and one draft, which are never kept`. In `sample_mailbox`, add right after the "Your order has shipped" mail:

```python
    # Mail the Marketing starter rule fits (an unsubscribe link, an offer in the subject).
    for subject, age in (("Big sale: 30% off shoes", 15), ("Spring offers just for you", 25)):
        add(
            make_mail(
                subject,
                sender="deals@shop.example.com",
                when=days(age),
                headers={"List-Unsubscribe": "<mailto:unsubscribe@shop.example.com>"},
            ),
            labels=("INBOX",),
            when=days(age),
        )
```

Run: `cd backend && uv run pytest tests/test_rules_running.py tests/test_setup_and_seed.py -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 3: The sample's counts in the browser tests**

- `frontend/e2e/helpers.ts`, `setUpMail`: the doc comment's "(the sample mailbox: 24 kept of 26)" becomes "(the sample mailbox: 26 kept of 28)"; `/^24 mails · .* · checked/` becomes `/^26 mails · .* · checked/`; "About 26 mails. The first backup takes a few hours. Nothing in Gmail changes." becomes "About 28 mails. The first backup takes a few hours. Nothing in Gmail changes."
- `frontend/e2e/smoke.spec.ts`: both `/^24 mails · .* · checked/` and `/^24 mails · /` become `26`; the comment "All mail lists the 24 kept mails as threads" becomes "the 26 kept mails"; the next line's `toHaveCount(22)` becomes `toHaveCount(24)`. The new mails are younger than a year, so the offload of "older than 1 year" (13 mails) and the other counts don't change.

- [ ] **Step 4: The browser test**

In `frontend/e2e/rules.spec.ts`, replace step 3 of the first test (from `// 3. Run the rules on the inbox` up to the line before `// 4. Undo it`) with:

```ts
  // 3. Run the rule from its card: it decides the Inbox with this rule alone, lists what it
  //    matched (all picked), and archives the mail on approval. No typed count for Archive.
  await page.getByRole("button", { name: "Run, Invoice" }).click();
  const run = page.getByRole("dialog", { name: "Run rules" });
  const matched = run.getByRole("list", { name: "Matched mail" });
  await expect(matched.getByRole("listitem")).toHaveCount(1, { timeout: 60_000 });
  await expect(matched).toContainText("Invoice October");
  await expect(matched).toContainText("subject mentions Invoice");
  await expect(run.getByText(/^Invoice would archive 1 mail\./)).toBeVisible();
  await expect(run.getByRole("checkbox", { name: "Pick all" })).toBeChecked();
  await run.getByRole("button", { name: "Archive 1 mail" }).click();
  await expect(run.getByText("Done. Applied 1, skipped 0, failed 0, left out 0.")).toBeVisible({ timeout: 60_000 });
  await run.getByRole("button", { name: "Close" }).click();
  await expect((await rows(page, "inbox")).getByRole("link", { name: /Invoice October/ })).toHaveCount(0); // archived
```

Then add this test right after the first test (before "a model that hits its limit pauses its connection until Resume now"):

```ts
// Runs after the first test, which points Sorting at the pretend model and backs up the sample
// mailbox. It doesn't call setUpMail: the first test's Delete forever can change the kept count.
test("Run rules on a search leaves out an unticked mail; Run on the Marketing card archives it", async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await ready(page);

  // 1. Add the starter rule Marketing.
  await page.goto("/mail/rules");
  await page.getByRole("button", { name: "Starter rules…" }).click();
  const starters = page.getByRole("dialog", { name: "Starter rules" });
  await starters.getByRole("checkbox", { name: /^Marketing/ }).check();
  await starters.getByRole("button", { name: "Add" }).click();
  await expect(page.getByText("Added 1 rule.")).toBeVisible();

  // 2. A search in the Inbox, Run rules with Marketing ticked, one mail unticked.
  await page.goto("/mail?view=inbox&q=from%3Adeals");
  await expect(page.getByRole("list", { name: "Mail" }).getByRole("listitem")).toHaveCount(2);
  await page.getByRole("button", { name: "Run rules", exact: true }).click();
  const picker = page.getByRole("dialog", { name: "Run rules" });
  await expect(picker.getByText("Run on: 2 mails matching from:deals")).toBeVisible();
  await expect(picker.getByRole("button", { name: "Run", exact: true })).toBeDisabled();
  await picker.getByRole("checkbox", { name: /^Marketing/ }).check();
  await picker.getByRole("button", { name: "Run", exact: true }).click();
  const run = page.getByRole("dialog", { name: "Run rules" });
  const matched = run.getByRole("list", { name: "Matched mail" });
  await expect(matched.getByRole("listitem")).toHaveCount(2, { timeout: 60_000 });
  await expect(run.getByText(/^Marketing would change 2 mails: Label Marketing, Archive\./)).toBeVisible();
  await expect(matched).toContainText("subject fits Marketing");
  await run.getByRole("checkbox", { name: "Pick Spring offers just for you" }).uncheck();
  await expect(run.getByRole("checkbox", { name: "Pick all" })).not.toBeChecked();
  await run.getByRole("button", { name: "Apply 1 change" }).click();
  await expect(run.getByText("Done. Applied 1, skipped 0, failed 0, left out 1.")).toBeVisible({ timeout: 60_000 });
  await run.getByRole("button", { name: "Close" }).click();
  const inbox = await rows(page, "inbox");
  await expect(inbox.getByRole("link", { name: /Big sale/ })).toHaveCount(0);
  await expect(inbox.getByRole("link", { name: /Spring offers just for you/ })).toBeVisible(); // left alone

  // 3. Run on the Marketing card covers the Inbox: it finds the mail left out before.
  await page.goto("/mail/rules");
  await page.getByRole("button", { name: "Run, Marketing" }).click();
  const cardRun = page.getByRole("dialog", { name: "Run rules" });
  const list = cardRun.getByRole("list", { name: "Matched mail" });
  await expect(list.getByRole("listitem")).toHaveCount(1, { timeout: 60_000 });
  await expect(list).toContainText("Spring offers just for you");
  await cardRun.getByRole("button", { name: "Apply 1 change" }).click();
  await expect(cardRun.getByText("Done. Applied 1, skipped 0, failed 0, left out 0.")).toBeVisible({ timeout: 60_000 });
  await cardRun.getByRole("button", { name: "Close" }).click();

  // 4. Both marketing mails are archived: in Archived, not in the Inbox.
  const archived = await rows(page, "archived");
  await expect(archived.getByRole("link", { name: /Big sale/ })).toBeVisible();
  await expect(archived.getByRole("link", { name: /Spring offers just for you/ })).toBeVisible();
  await expect((await rows(page, "inbox")).getByRole("link", { name: /Spring offers/ })).toHaveCount(0);

  expect(problems, problems.join("\n")).toEqual([]);
});
```

Run it the way the rules project runs (the `rules` project in `frontend/playwright.config.ts`, after the smoke tests), against a CleanSync built from this branch with the sample mailbox and the pretend model, freshly seeded so the sample has its two new mails. Expected: the smoke tests and all three rules tests pass.

- [ ] **Step 5: The docs**

**`docs/mail-rules.md`:**
- In §3, "(see "Run rules on older mail" below)" becomes "(see "Running rules by hand" below)".
- Replace §4 ("## 4. Run rules on older mail" and its text, up to "## 5.") with:

```markdown
## 4. Running rules by hand

Sort new inbox mail only looks at new mail. To run rules on mail you already have:

- **Run on a rule card** (Mail → Rules) runs that one rule over your **Inbox**: every mail in Gmail with the Inbox label. Each mail is checked against this rule alone (its remembered senders, its fixed conditions, then the model with this rule as the only choice). Your other rules, and their order, play no part. The button is greyed out while the rule is off, and when the rule needs the model and no Sorting model is set up; the reason shows next to it. For mail outside the Inbox, use a search.
- **Run rules on a search or a pick.** In Gmail's views (Inbox, Starred, Important, Sent, Archived, All mail and your labels), **Run rules** shows above the list when the search box isn't empty, and on the bar when you have picked mail (by hand, or with **Pick all**). It runs on every mail of the search in that view, or on the picked mail. It isn't offered on a view with no search, or in Suggested, Offloaded or Deleted in Gmail. First tick the rules to run (only rules that are on are listed). Each mail gets at most one rule: the first ticked rule that fits, in the order of your rules.

Then:

1. **Deciding.** CleanSync decides each mail using only its own copy. Nothing changes in Gmail yet. You can close the window: the run keeps deciding, and the Rules page says so, with an **Open** button that shows it again.
2. **The list of matched mails.** It says what each rule would do and how many mails didn't fit, then lists the matched mails 50 at a time (**Show more** for the rest), each with its sender, subject, date and the reason. With several rules, each row shows its rule, and **Show** narrows the list to one rule. Every mail is picked at first (**Pick all**); untick the ones to leave alone. The button names the change and the count, for example **Archive 412 mails**, or **Apply 442 changes** when several rules or actions are involved. Pressing it is your approval, whatever each rule's mode is. If any picked mail would be offloaded, you first type the number of those mails.
3. **Making the changes.** The changes are made one by one. You can close the window; it keeps going. **Stop** stops it. A mail that changed after it was decided (or whose rule you changed meanwhile) is left as it is and counted as skipped. The end shows how many were applied, skipped (with the reasons), failed, and left out by you. Mail you left out stays as it is.

Mail that sorting is still deciding, and mail you moved back yourself, are skipped. Closing the window before you approve drops the run; nothing is changed. Only one run at a time per mailbox: starting a new one drops a run that is waiting for you, but while changes are being made you have to wait or press **Stop** first. One run covers at most 20,000 mails. Narrow the search for more.
```

**`docs/architecture.md`:** replace the sentence `"Run rules on…" is a rule job: the same worker decides every mail first as a preview, and makes the changes only after the owner types the count.` with `Running rules by hand (Run on a rule card, or Run rules on a search or a pick) is a rule job with its own list of rules (\`rule_jobs.rule_ids\`): the same worker decides every mail with only those rules, the owner approves from the list of matched mails, and the mails left out are recorded as rejected.`

**`docs/limitations.md`**, Mail rules section: after "A run of rules on older mail covers at most 20,000 mails at a time." add "Run on a rule card covers the Inbox only; for other mail, search and use Run rules."

Then: `grep -rn "Run rules on" docs/*.md`. Expected: nothing.

- [ ] **Step 6: Full checks**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && cd ../frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/devtools/fake_gmail.py backend/tests/test_rules_running.py frontend/e2e/helpers.ts frontend/e2e/smoke.spec.ts frontend/e2e/rules.spec.ts docs/mail-rules.md docs/architecture.md docs/limitations.md
git commit -m "test(e2e): run a rule from its card and on a search with a mail left out; docs for running rules

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
