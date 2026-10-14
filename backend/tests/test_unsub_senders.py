from datetime import UTC, datetime, timedelta
from itertools import count

from app.models import MailMessage, UnsubscribeSender
from app.services.unsubscribe.senders import (
    list_senders,
    newest_with_link,
    sender_addresses,
    set_status,
    suggested_count,
)

T0 = datetime(2026, 10, 1, tzinfo=UTC)
OWN = "me@example.com"
_n = count()


def mail(
    db,
    acc,
    sender,
    *,
    days=0,
    unread=True,
    labels=("INBOX",),
    lu="<https://x.example/u>",
    state="in_mail",
    size=100,
    name="Shop",
    commit=True,
):
    m = MailMessage(
        account_id=acc.id,
        provider_id=f"p{next(_n)}",
        thread_id="t",
        from_addr=f"{name} <{sender}>",
        subject="s",
        date=T0 - timedelta(days=days),
        size=size,
        file_size=size,
        sha256="0" * 64,
        labels=list(labels),
        unread=unread,
        file_path="x",
        state=state,
        list_unsubscribe=lu,
        sender=sender,
        headers_scanned=True,
    )
    db.add(m)
    if commit:
        db.commit()
    return m


def rows(db, acc, **kw):
    return list_senders(db, acc.id, OWN, **kw)[0]


def test_groups_by_sender_with_stats(db, make_account):
    acc = make_account()
    mail(db, acc, "shop@x.com", days=3, unread=False, labels=["Label_1"], name="Shop Old")
    mail(db, acc, "shop@x.com", days=2)
    mail(db, acc, "shop@x.com", days=1, labels=["Label_1"])
    mail(db, acc, "shop@x.com", days=0, name="Shop New")
    mail(db, acc, "news@y.com", days=5)
    got, total = list_senders(db, acc.id, OWN, tab="all")
    assert total == 2 and len(got) == 2
    shop = next(r for r in got if r.address == "shop@x.com")
    assert shop.count == 4
    assert shop.read_pct == 25
    assert shop.inbox_pct == 50
    assert shop.name == "Shop New"
    assert shop.last_at.replace(tzinfo=UTC) == T0
    assert shop.bytes == 400
    assert shop.newest_unsubscribe == "<https://x.example/u>"
    assert shop.status is None


def test_left_out(db, make_account):
    acc = make_account()
    mail(db, acc, "nolink@x.com", lu=None)
    mail(db, acc, OWN)
    mail(db, acc, "sent@x.com", labels=["SENT"])
    mail(db, acc, "draft@x.com", labels=["DRAFT"])
    mail(db, acc, None)
    mail(db, acc, "ok@x.com")
    assert [r.address for r in rows(db, acc, tab="all")] == ["ok@x.com"]


def test_suggested_rule(db, make_account):
    acc = make_account()
    for _ in range(3):
        mail(db, acc, "yes@x.com")
    for i in range(3):
        mail(db, acc, "third@x.com", unread=i != 0)
    for _ in range(2):
        mail(db, acc, "few@x.com")
    for _ in range(5):
        mail(db, acc, "kept@x.com")
    set_status(db, acc.id, "kept@x.com", "kept", now=T0)
    assert [r.address for r in rows(db, acc, tab="suggested")] == ["yes@x.com"]
    assert suggested_count(db, acc.id, OWN) == 1


def test_status_tabs(db, make_account):
    acc = make_account()
    mail(db, acc, "a@x.com")
    set_status(db, acc.id, "a@x.com", "unsubscribed", method="one_click", now=T0)
    assert [r.address for r in rows(db, acc, tab="unsubscribed")] == ["a@x.com"]
    assert [r.address for r in rows(db, acc, tab="all")] == ["a@x.com"]
    assert rows(db, acc, tab="kept") == []
    assert rows(db, acc, tab="all")[0].method == "one_click"
    set_status(db, acc.id, "a@x.com", None, now=T0)
    assert rows(db, acc, tab="unsubscribed") == []
    assert rows(db, acc, tab="all")[0].status is None
    assert db.query(UnsubscribeSender).count() == 1


def test_filter_id_is_kept_unless_given(db, make_account):
    acc = make_account()
    set_status(db, acc.id, "a@x.com", "auto_archived", filter_id="f1", now=T0)
    set_status(db, acc.id, "a@x.com", None, now=T0)
    assert db.query(UnsubscribeSender).one().filter_id == "f1"
    set_status(db, acc.id, "a@x.com", "kept", filter_id=None, now=T0)
    assert db.query(UnsubscribeSender).one().filter_id is None


def test_sort_and_search(db, make_account):
    acc = make_account()
    mail(db, acc, "big@x.com", days=9, size=900, unread=False, name="Big")
    for i in range(3):
        mail(db, acc, "many@x.com", days=5 + i, size=10, name="Many")
    for i in range(5):  # most mails, but all read: the "unread" order differs from "mails"
        mail(db, acc, "allread@x.com", days=20 + i, size=10, unread=False, name="Read")
    mail(db, acc, "fresh@x.com", days=0, size=50, name="Shop New")
    mail(db, acc, "mix@x.com", days=7, size=50, unread=False, name="Mix")
    mail(db, acc, "mix@x.com", days=7, size=50, unread=True, name="Mix")

    def order(sort):
        return [r.address for r in rows(db, acc, tab="all", sort=sort)]

    assert order("mails")[0] == "allread@x.com"
    assert order("unread")[0] == "many@x.com"
    assert order("newest")[0] == "fresh@x.com"
    assert order("size")[0] == "big@x.com"
    assert [r.address for r in rows(db, acc, tab="all", q="fresh")] == ["fresh@x.com"]
    assert [r.address for r in rows(db, acc, tab="all", q="Shop New")] == ["fresh@x.com"]
    assert rows(db, acc, tab="all", q="%") == []


def test_paging_and_pick_all(db, make_account):
    acc = make_account()
    for i in range(120):
        mail(db, acc, f"s{i:03}@x.com", commit=False)
    db.commit()
    page, total = list_senders(db, acc.id, OWN, tab="all", page=2, page_size=50)
    assert len(page) == 50 and total == 120
    assert len(sender_addresses(db, acc.id, OWN, tab="all", q="")) == 120


def test_counts_include_mail_out_of_gmail(db, make_account):
    acc = make_account()
    mail(db, acc, "a@x.com")
    mail(db, acc, "a@x.com", state="offloaded", labels=["INBOX"])
    r = rows(db, acc, tab="all")[0]
    assert r.count == 2 and r.inbox_pct == 50


def test_newest_with_link(db, make_account):
    acc = make_account()
    mail(db, acc, "a@x.com", days=3, lu="<https://old.example/u>")
    new = mail(db, acc, "a@x.com", days=0, lu="<https://new.example/u>")
    got = newest_with_link(db, acc.id, OWN, "a@x.com")
    assert got.newest_unsubscribe == "<https://new.example/u>"
    assert got.newest_mail_id == new.id
    assert newest_with_link(db, acc.id, OWN, "none@x.com") is None


def test_api_list_and_mark(auth_client, db, make_account):
    acc = make_account(email=OWN)
    for _ in range(3):
        mail(db, acc, "a@x.com")
    r = auth_client.get(
        "/api/mail/unsubscribe/senders", params={"account_id": acc.id, "tab": "all"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1 and body["suggested"] == 1
    assert body["scan"] == {"scanned": 3, "total": 3}
    item = body["items"][0]
    assert item["address"] == "a@x.com" and item["newest_unsubscribe"]
    for bad in ({"tab": "nope"}, {"sort": "nope"}):
        r = auth_client.get("/api/mail/unsubscribe/senders", params={"account_id": acc.id, **bad})
        assert r.status_code == 400
    r = auth_client.get("/api/mail/unsubscribe/senders", params={"account_id": 9999})
    assert r.status_code == 404
    r = auth_client.post(
        "/api/mail/unsubscribe/mark",
        json={"account_id": acc.id, "addresses": ["a@x.com"], "status": "unsubscribed"},
    )
    assert r.status_code == 200 and r.json() == {"changed": 1}
    assert db.query(UnsubscribeSender).one().method == "manual"
    r = auth_client.get(
        "/api/mail/unsubscribe/senders", params={"account_id": acc.id, "tab": "unsubscribed"}
    )
    assert r.json()["total"] == 1
    r = auth_client.post(
        "/api/mail/unsubscribe/mark",
        json={"account_id": acc.id, "addresses": ["a@x.com"], "status": None},
    )
    assert r.json() == {"changed": 1}
    r = auth_client.get(
        "/api/mail/unsubscribe/senders", params={"account_id": acc.id, "tab": "unsubscribed"}
    )
    assert r.json()["total"] == 0


def test_api_mark_rejects_other_statuses(auth_client, db, make_account):
    acc = make_account(email=OWN)
    r = auth_client.post(
        "/api/mail/unsubscribe/mark",
        json={"account_id": acc.id, "addresses": ["a@x.com"], "status": "kept"},
    )
    assert r.status_code == 422


def test_empty(db, make_account):
    acc = make_account()
    assert list_senders(db, acc.id, OWN, tab="all") == ([], 0)


def test_api_mark_batch_dedupes_and_never_makes_empty_rows(auth_client, db, make_account):
    acc = make_account(email=OWN)
    r = auth_client.post(
        "/api/mail/unsubscribe/mark",
        json={"account_id": acc.id, "addresses": ["A@x.com", "a@x.com", "b@x.com"], "status": None},
    )
    assert r.json() == {"changed": 0}
    assert db.query(UnsubscribeSender).count() == 0
    r = auth_client.post(
        "/api/mail/unsubscribe/mark",
        json={
            "account_id": acc.id,
            "addresses": ["A@x.com", "a@x.com", "b@x.com"],
            "status": "unsubscribed",
        },
    )
    assert r.json() == {"changed": 2}
    assert {u.address for u in db.query(UnsubscribeSender)} == {"a@x.com", "b@x.com"}
    again = auth_client.post(
        "/api/mail/unsubscribe/mark",
        json={"account_id": acc.id, "addresses": ["a@x.com"], "status": "unsubscribed"},
    )
    assert again.json() == {"changed": 0}
    cleared = auth_client.post(
        "/api/mail/unsubscribe/mark",
        json={"account_id": acc.id, "addresses": ["a@x.com"], "status": None},
    )
    assert cleared.json() == {"changed": 1}
    db.expire_all()
    row = db.query(UnsubscribeSender).filter_by(address="a@x.com").one()
    assert row.status is None and row.method is None
