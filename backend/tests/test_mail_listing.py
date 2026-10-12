from datetime import UTC, datetime, timedelta

from app.devtools.fake_gmail import make_mail
from app.models import MailLabel
from app.models.enums import MailState
from app.services.mail import index
from app.services.mail.adapter import MailMeta
from app.services.mail.store import parse_eml

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _add(
    db,
    acc,
    mid,
    *,
    thread=None,
    days=1,
    labels=("INBOX",),
    state=MailState.IN_MAIL,
    unread=False,
    subject=None,
    sender="friend@example.com",
):
    when = NOW - timedelta(days=days)
    labels = (*labels, "UNREAD") if unread else labels
    raw = make_mail(subject or f"Subject {mid}", sender=sender, when=when)
    row = index.add_message(
        db,
        acc.id,
        MailMeta(mid, thread or mid, labels, when, 1000, "1", f"snippet {mid}"),
        parse_eml(raw),
        f"x/{mid}.eml",
        len(raw),
        "0" * 64,
        NOW,
    )
    row.state = state
    db.commit()
    return row


def _page(client, acc, **params):
    r = client.get("/api/mail/threads", params={"account_id": acc.id, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_threads_are_grouped_newest_first_with_their_marks(auth_client, db, make_account):
    acc = make_account()
    _add(db, acc, "a1", thread="t1", days=5, subject="Project plan")
    _add(
        db,
        acc,
        "a2",
        thread="t1",
        days=4,
        subject="Re: Project plan",
        sender="me@example.com",
        labels=("SENT",),
        unread=True,
    )
    _add(db, acc, "b1", thread="t2", days=1, state=MailState.OFFLOADED)
    _add(db, acc, "c1", thread="t3", days=9, state=MailState.DELETED)
    page = _page(auth_client, acc)
    assert [t["thread_id"] for t in page["threads"]] == ["t2", "t1", "t3"]
    t1 = page["threads"][1]
    assert (
        t1["count"] == 2
        and t1["subject"] == "Re: Project plan"
        and t1["from_addr"] == "me@example.com"
    )
    assert t1["unread"] is True and t1["snippet"] == "snippet a2"
    assert page["threads"][0]["offloaded"] is True and page["threads"][2]["deleted"] is True
    assert page["total"] == 4 and page["total_bytes"] == 4000 and page["next_cursor"] is None
    inbox = _page(auth_client, acc, view="inbox")
    assert [t["thread_id"] for t in inbox["threads"]] == ["t2", "t1", "t3"]
    assert inbox["threads"][1]["count"] == 1  # only the mail in the inbox counts


def test_paging_neither_repeats_nor_skips_when_mail_arrives(auth_client, db, make_account):
    acc = make_account()
    for i in range(7):
        _add(db, acc, f"m{i}", days=10 + i)
    first = _page(auth_client, acc, limit=3)
    assert first["next_cursor"]
    _add(db, acc, "new", days=0)  # new mail arrives between pages
    second = _page(auth_client, acc, limit=3, cursor=first["next_cursor"])
    assert second["total"] is None  # totals come with the first page only
    third = _page(auth_client, acc, limit=3, cursor=second["next_cursor"])
    seen = [t["thread_id"] for p in (first, second, third) for t in p["threads"]]
    assert seen == [f"m{i}" for i in range(7)]
    assert third["next_cursor"] is None


def test_bad_searches_and_cursors_are_plain_422s(auth_client, make_account):
    acc = make_account()
    r = auth_client.get("/api/mail/threads", params={"account_id": acc.id, "q": "larger:abc"})
    assert r.status_code == 422
    assert r.json()["detail"] == '"abc" is not a size. Use a number with K, M or G, like 5M.'
    r = auth_client.get("/api/mail/threads", params={"account_id": acc.id, "cursor": "!!"})
    assert (
        r.status_code == 422
        and r.json()["detail"] == "That page of the list is no longer valid. Start from the top."
    )


def test_counts_for_views_and_labels(auth_client, db, make_account):
    acc = make_account()
    db.add_all(
        [
            MailLabel(account_id=acc.id, provider_label_id="Label_1", name="Work", type="user"),
            MailLabel(account_id=acc.id, provider_label_id="Label_2", name="Receipts", type="user"),
            MailLabel(account_id=acc.id, provider_label_id="INBOX", name="INBOX", type="system"),
        ]
    )
    db.commit()
    _add(db, acc, "a", labels=("INBOX", "Label_1", "STARRED"))
    _add(db, acc, "b", labels=("SENT", "Label_1"))
    _add(db, acc, "c", labels=("IMPORTANT",), state=MailState.OFFLOADED)
    _add(db, acc, "d", labels=("INBOX",), state=MailState.DELETED)
    body = auth_client.get("/api/mail/counts", params={"account_id": acc.id}).json()
    assert body["views"] == {
        "all": 4,
        "inbox": 2,
        "sent": 1,
        "starred": 1,
        "important": 1,
        "offloaded": 1,
        "deleted": 1,
        "archive": 0,
        "suggested": 0,
    }
    assert body["labels"] == [
        {"id": "Label_2", "name": "Receipts", "count": 0},
        {"id": "Label_1", "name": "Work", "count": 2},
    ]


def test_empty_cursor_counts_like_no_cursor_and_a_huge_search_is_refused(
    auth_client, db, make_account
):
    acc = make_account()
    _add(db, acc, "a")
    r = auth_client.get(
        "/api/mail/threads", params={"account_id": acc.id, "cursor": "", "label": "", "q": ""}
    )
    assert r.status_code == 200 and r.json()["total"] == 1
    r = auth_client.get("/api/mail/threads", params={"account_id": acc.id, "q": "a" * 1001})
    assert (
        r.status_code == 400
        and r.json()["detail"] == "That search is too long. Keep it under 1,000 characters."
    )


def test_a_cursor_with_a_made_up_thread_id_is_a_plain_422(auth_client, make_account):
    from app.services.mail.listing import encode_cursor

    acc = make_account()
    cursor = encode_cursor(NOW, "x'; drop table")
    r = auth_client.get("/api/mail/threads", params={"account_id": acc.id, "cursor": cursor})
    assert r.status_code == 422


def test_threads_with_the_same_date_each_show_once_across_pages(auth_client, db, make_account):
    acc = make_account()
    for i in range(7):
        _add(db, acc, f"t{i}", days=3)  # every thread has exactly the same newest date
    _add(db, acc, "newer", days=1)
    _add(db, acc, "older", days=5)
    seen: list[str] = []
    cursor = None
    while True:
        page = _page(auth_client, acc, limit=3, **({"cursor": cursor} if cursor else {}))
        seen += [t["thread_id"] for t in page["threads"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen[0] == "newer" and seen[-1] == "older"
    assert sorted(seen) == sorted({*seen}) and len(seen) == 9


def test_counts_read_the_label_tally_once(auth_client, db, make_account, monkeypatch):
    from app.services.mail import listing

    acc = make_account()
    _add(db, acc, "a", labels=("INBOX",))
    calls = []
    real = listing._label_tally

    def counting(db, account_id):
        calls.append(account_id)
        return real(db, account_id)

    monkeypatch.setattr(listing, "_label_tally", counting)
    body = auth_client.get("/api/mail/counts", params={"account_id": acc.id}).json()
    assert body["views"]["inbox"] == 1
    assert calls == [acc.id]
