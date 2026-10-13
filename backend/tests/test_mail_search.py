from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.devtools.fake_gmail import MINI_PDF, make_mail
from app.models import MailLabel, MailMessage
from app.models.enums import MailState
from app.services.mail import index
from app.services.mail.adapter import MailMeta
from app.services.mail.search import (
    MailQuery,
    SearchError,
    fts_match,
    mail_conditions,
    parse_query,
)
from app.services.mail.store import parse_eml

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def test_operators_become_fields():
    q = parse_query(
        'invoice from:bank@example.com to:"Alice Doe" label:Receipts has:attachment larger:5M '
        "smaller:1.5g before:2026-01-31 after:2024-01-01 is:unread is:starred is:offloaded is:deleted",
        now=NOW,
    )
    assert q.words == ["invoice"]
    assert q.senders == ["bank@example.com"] and q.recipients == ["Alice Doe"]
    assert q.labels == ["Receipts"] and q.has_attachment
    assert q.larger == 5 * 1024**2 and q.smaller == int(1.5 * 1024**3)
    assert q.before == datetime(2026, 1, 31, tzinfo=UTC)
    assert q.after == datetime(2024, 1, 1, tzinfo=UTC)
    assert q.unread and q.starred and q.offloaded and q.deleted


def test_ages_count_back_from_now_and_the_stricter_limit_wins():
    q = parse_query("older_than:1y newer_than:30d before:2025-12-01", now=NOW)
    assert q.before == datetime(2025, 10, 10, 12, 0, tzinfo=UTC)  # 1y: 365 days, earlier than Dec 1
    assert q.after == NOW - timedelta(days=30)
    assert parse_query("older_than:6m", now=NOW).before == NOW - timedelta(days=180)


def test_quotes_unknown_operators_and_case():
    q = parse_query('"two words" FROM:Bank http://example.com foo:bar', now=NOW)
    assert q.words == ["two words", "http://example.com", "foo:bar"]
    assert q.senders == ["Bank"]
    assert parse_query("", now=NOW).is_empty()
    assert parse_query("   ", now=NOW) == MailQuery()


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("larger:abc", '"abc" is not a size. Use a number with K, M or G, like 5M.'),
        ("before:2024-13-01", '"2024-13-01" is not a date. Use YYYY-MM-DD, like 2024-01-31.'),
        ("older_than:soon", '"soon" is not an age. Use days, months or years, like 30d, 6m or 1y.'),
        ("is:read", '"is:" knows unread, starred, offloaded and deleted.'),
        ("has:pictures", '"has:" only knows has:attachment.'),
        ("from:", '"from:" needs something after it.'),
    ],
)
def test_bad_values_say_what_to_type(text, says):
    with pytest.raises(SearchError) as e:
        parse_query(text, now=NOW)
    assert str(e.value) == says


def _add(
    db,
    acc,
    mid,
    subject,
    *,
    sender="friend@example.com",
    labels=("INBOX",),
    days=10,
    state=MailState.IN_MAIL,
    attach=False,
    size=1000,
    text="hello",
):
    when = NOW - timedelta(days=days)
    raw = make_mail(
        subject,
        sender=sender,
        text=text,
        when=when,
        attachments=(("a.pdf", "application/pdf", MINI_PDF),) if attach else (),
    )
    row = index.add_message(
        db,
        acc.id,
        MailMeta(mid, mid, labels, when, size, "1", ""),
        parse_eml(raw),
        f"x/{mid}.eml",
        len(raw),
        "0" * 64,
        NOW,
    )
    row.state = state
    db.commit()
    return row


def _found(db, acc, **kw):
    conds = mail_conditions(db, acc.id, now=NOW, **kw)
    return sorted(db.scalars(select(MailMessage.provider_id).where(*conds)))


def test_conditions_find_the_right_mail(db, make_account):
    acc = make_account()
    db.add(MailLabel(account_id=acc.id, provider_label_id="Label_2", name="Receipts", type="user"))
    db.commit()
    _add(
        db,
        acc,
        "m1",
        "Invoice October",
        sender="billing@example.com",
        labels=("INBOX", "Label_2"),
        attach=True,
        size=6 * 1024**2,
        text="Your invoice",
    )
    _add(db, acc, "m2", "Holiday plans", labels=("INBOX", "STARRED", "UNREAD"), days=400)
    _add(db, acc, "m3", "Old receipt", labels=("Label_2",), days=600, state=MailState.OFFLOADED)
    _add(
        db,
        acc,
        "m4",
        "Hello",
        sender="me@example.com",
        labels=("SENT",),
        days=50,
        state=MailState.DELETED,
    )
    assert _found(db, acc, q="invoic") == ["m1"]  # words match by their start
    assert _found(db, acc, q="from:billing") == ["m1"]
    assert _found(db, acc, q="label:receipts") == ["m1"]
    assert _found(db, acc, view="offloaded", q="label:Label_2 older_than:1y") == ["m3"]
    assert _found(db, acc, q="has:attachment larger:5M") == ["m1"]
    assert _found(db, acc, q="smaller:5M") == ["m2"]
    assert _found(db, acc, q="is:unread is:starred") == ["m2"]
    assert _found(db, acc, view="offloaded", q="is:offloaded") == ["m3"]
    assert _found(db, acc, view="offloaded") == ["m3"]
    assert _found(db, acc, view="deleted", q="is:deleted") == ["m4"]
    assert _found(db, acc, view="deleted") == ["m4"]
    assert _found(db, acc, view="inbox") == ["m1", "m2"]
    assert _found(db, acc, view="sent") == []
    assert _found(db, acc, view="starred") == ["m2"]
    assert _found(db, acc, view="all", label="Label_2") == ["m1"]
    assert _found(db, acc, q="label:nosuchlabel") == []
    assert _found(db, acc, q='"weird \\" quote %_ value"') == []  # odd characters are safe
    with pytest.raises(SearchError, match="unknown view"):
        _found(db, acc, view="spam")


@pytest.mark.parametrize("text", ["a\x00b", "\x00", "hello\x00", '"a\x00b"', "from:x\x00y"])
def test_nul_bytes_never_break_the_search(db, make_account, text):
    acc = make_account()
    _add(db, acc, "m1", "Hello world")
    _found(db, acc, q=text)  # must not raise
    assert "\x00" not in fts_match([text])


@pytest.mark.parametrize(
    "text",
    [
        "older_than:99999999999d",
        "older_than:9999999y",
        "newer_than:999999y",
        "newer_than:" + "9" * 5000 + "d",
    ],
)
def test_huge_numbers_give_a_plain_error(text):
    with pytest.raises(SearchError):
        parse_query(text, now=NOW)


def test_huge_sizes_are_capped_and_searchable(db, make_account):
    acc = make_account()
    _add(db, acc, "m1", "Hello")
    assert parse_query("larger:" + "9" * 400, now=NOW).larger == 2**63 - 1
    q = parse_query("smaller:99999999999999999999G", now=NOW)
    assert q.smaller == 2**63 - 1
    assert _found(db, acc, q="smaller:99999999999999999999G") == ["m1"]
    assert _found(db, acc, q="larger:99999999999999999999G") == []


@pytest.mark.parametrize("text", ["older_than:٣d", "before:٢٠٢٤-٠١-٣١"])
def test_only_plain_digits_count(text):
    with pytest.raises(SearchError):
        parse_query(text, now=NOW)


def test_label_names_match_whatever_their_case_outside_plain_ascii(db, make_account):
    acc = make_account()
    db.add_all(
        [
            MailLabel(
                account_id=acc.id, provider_label_id="Label_7", name="Équipe Paris", type="user"
            ),
            MailLabel(account_id=acc.id, provider_label_id="Label_8", name="Straße", type="user"),
        ]
    )
    db.commit()
    _add(db, acc, "m1", "Réunion", labels=("INBOX", "Label_7"))
    _add(db, acc, "m2", "Brief", labels=("INBOX", "Label_8"))
    _add(db, acc, "m3", "Other", labels=("INBOX",))
    assert _found(db, acc, q='label:"Équipe Paris"') == ["m1"]
    assert _found(db, acc, q='label:"équipe paris"') == ["m1"]
    assert _found(db, acc, q='label:"ÉQUIPE PARIS"') == ["m1"]
    assert _found(db, acc, q="label:STRASSE") == ["m2"]  # casefold, not just lower
    assert _found(db, acc, q="label:Label_7") == ["m1"]
    assert _found(db, acc, q="label:INBOX") == ["m1", "m2", "m3"]
