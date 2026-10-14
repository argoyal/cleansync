"""Issue #16: on a 50,000-mail account the list, counts, totals and status stay fast.

The query-plan checks are the real guard (they don't depend on the machine). The timings are
loose so a slow CI machine passes; run with -s to see them."""

import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.services.mail import listing
from app.services.mail.search import mail_conditions

N = 50_000
# Real mails make big rows: a 300-character snippet, addresses, a subject, an attachment list.
SNIPPET = "Hello, here is the update on the project we talked about last week. " * 5
ATTACHMENTS = '[{"name": "invoice-2026-01.pdf", "type": "application/pdf", "size": 182734}]'
T0 = datetime(2026, 1, 1, tzinfo=UTC)
SLOW_MS = 1000


def _labels(i: int) -> str:
    if i % 7 == 0:  # in Gmail, out of the inbox and not sent: the Archived list
        return '["Label_1", "CATEGORY_UPDATES"]'
    if i % 4:
        return '["INBOX", "IMPORTANT", "CATEGORY_UPDATES"]'
    return '["Label_1", "SENT", "CATEGORY_PERSONAL"]'


def _state(i: int) -> str:
    """Every tenth mail was offloaded and every thirteenth deleted in Gmail: the lists of
    mail in Gmail must stay quick with that mixed in."""
    if i % 10 == 9:
        return "offloaded"
    return "deleted" if i % 13 == 12 else "in_mail"


@pytest.fixture
def big_account(db, make_account):
    acc = make_account()
    rows = []
    for i in range(N):
        stamp = (T0 - timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S.000000")
        rows.append(
            {
                "a": acc.id,
                "p": f"m{i}",
                "t": f"t{i // 3}",
                "d": stamp,
                "l": _labels(i),
                "st": _state(i),
                "subj": f"Re: Quarterly numbers for the team, round {i}",
                "who": f"Person {i % 500} <person{i % 500}@example.com>",
                "snip": SNIPPET[:300],
                "att": ATTACHMENTS,
                "s": 1000 + i,
            }
        )
    db.execute(
        text(
            "INSERT INTO mail_messages (account_id, provider_id, thread_id, from_addr, to_addrs, "
            "cc_addrs, subject, date, size, file_size, sha256, labels, unread, starred, "
            "has_attachments, attachments, snippet, file_path, state, first_seen_at, updated_at) "
            "VALUES (:a, :p, :t, :who, :who, :who, :subj, :d, :s, :s, '', :l, 0, 0, 1, :att, :snip, 'x', "
            ":st, :d, :d)"
        ),
        rows,
    )
    db.commit()
    db.execute(text("ANALYZE"))
    db.commit()
    return acc


def _timed(fn):
    t = time.perf_counter()
    r = fn()
    return r, (time.perf_counter() - t) * 1000


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"view": "inbox"},
        {"label": "Label_1"},
        {"view": "sent"},
        {"view": "archived"},
        {"view": "offloaded"},
    ],
)
def test_first_and_second_pages_are_quick(auth_client, big_account, params):
    def get(**more):
        return auth_client.get(
            "/api/mail/threads", params={"account_id": big_account.id, **params, **more}
        )

    r1, ms1 = _timed(get)
    assert r1.status_code == 200 and len(r1.json()["threads"]) == 50
    r2, ms2 = _timed(lambda: get(cursor=r1.json()["next_cursor"]))
    assert r2.status_code == 200 and len(r2.json()["threads"]) == 50
    assert not {t["thread_id"] for t in r1.json()["threads"]} & {
        t["thread_id"] for t in r2.json()["threads"]
    }
    print(f"threads {params}: page 1 {ms1:.0f} ms, page 2 {ms2:.0f} ms")
    assert ms1 < SLOW_MS and ms2 < SLOW_MS


@pytest.mark.parametrize("path", ["/api/mail/counts", "/api/mail/status", "/api/mail/totals"])
def test_counts_status_and_totals_are_quick(auth_client, big_account, path):
    r, ms = _timed(lambda: auth_client.get(path, params={"account_id": big_account.id}))
    assert r.status_code == 200, r.text
    print(f"{path}: {ms:.0f} ms")
    assert ms < SLOW_MS


def _plan(db, stmt) -> str:
    compiled = stmt.compile(db.get_bind(), compile_kwargs={"literal_binds": True})
    return " | ".join(str(r[-1]) for r in db.execute(text(f"EXPLAIN QUERY PLAN {compiled}")))


@pytest.mark.parametrize("view", ["all", "inbox", "archived", "offloaded"])
def test_the_list_walk_reads_an_index_in_order(db, big_account, view):
    conds = mail_conditions(db, big_account.id, view=view, now=T0)
    plan = _plan(db, listing.walk_statement(conds, (T0, "t5", 10)))
    assert "TEMP B-TREE" not in plan, plan  # no sort of the whole mailbox
    assert "ix_mail_messages_account_date_thread" in plan, plan


def test_offload_preview_and_start_rows_stay_fast(db, big_account, monkeypatch):
    """§9: 50,000 mails, no file touched, planning and the bulk insert stay quick."""
    from app.models import MailOffload
    from app.services.mail import offload_plan
    from app.services.mail.offload_plan import Pick, insert_items, plan_offload

    def _touched(*_a, **_k):
        raise AssertionError("file touched")

    monkeypatch.setattr(offload_plan, "eml_path", _touched)
    acc = big_account
    pick = Pick(q="from:example.com", view="all")
    prev, ms = _timed(
        lambda: plan_offload(
            db, None, acc, pick, min_age_days=1, now=datetime(2026, 10, 14, tzinfo=UTC)
        )
    )
    assert prev.count > 30_000
    assert ms < 15_000
    off = MailOffload(account_id=acc.id, filter_text="x", total=prev.count)
    db.add(off)
    db.flush()
    _, ms2 = _timed(lambda: insert_items(db, off.id, prev.message_ids))
    db.commit()
    assert ms2 < 15_000


def test_unsubscribe_sender_list_is_quick_and_uses_its_index(db, big_account):
    """0.5.2 §3.2: the sender list is one GROUP BY; half the mail has a link."""
    from app.services.unsubscribe.senders import SENDERS_SQL, list_senders

    db.execute(
        text(
            "UPDATE mail_messages SET sender = 'person' || (id % 500) || '@example.com', "
            "list_unsubscribe = '<https://e.example/u>', headers_scanned = 1 WHERE id % 2 = 0"
        )
    )
    db.commit()
    db.execute(text("ANALYZE"))
    db.commit()
    for tab in ("all", "suggested"):
        (rows, total), ms = _timed(
            lambda tab=tab: list_senders(db, big_account.id, "me@example.com", tab=tab)
        )
        print(f"unsubscribe senders {tab}: {len(rows)} of {total}, {ms:.0f} ms")
        assert ms < SLOW_MS * 2
    sql = SENDERS_SQL.format(search="", having="")
    plan = " | ".join(
        str(r[-1])
        for r in db.execute(
            text(f"EXPLAIN QUERY PLAN {sql}"),
            {"account_id": big_account.id, "own": "me@example.com", "in_mail": "in_mail"},
        )
    )
    assert "ix_mail_unsub_sender" in plan, plan
