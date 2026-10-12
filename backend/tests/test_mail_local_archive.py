from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.devtools.fake_gmail import make_mail
from app.models import MailMessage, RuleDecision
from app.models.enums import DecisionHow, DecisionStatus, MailState
from app.services.mail.index import apply_labels, mark_deleted
from app.services.mail.listing import view_counts
from app.services.mail.search import SearchError, mail_conditions
from tests.rules_helpers import keep

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def ids(db, acc, **kw):
    conds = mail_conditions(db, acc.id, now=NOW, **kw)
    return {r.subject for r in db.scalars(select(MailMessage).where(*conds))}


def setup(db, layout, acc):
    a = keep(db, layout, acc, make_mail("Kept invoice"))
    b = keep(db, layout, acc, make_mail("Archived invoice"))
    b.state = MailState.LOCAL_ARCHIVE
    db.commit()
    return a, b


def test_archive_is_hidden_from_main_views_and_search(db, layout, make_account):
    acc = make_account()
    setup(db, layout, acc)
    assert ids(db, acc) == {"Kept invoice"}
    assert ids(db, acc, q="invoice") == {"Kept invoice"}
    assert ids(db, acc, view="inbox") == {"Kept invoice"}
    assert ids(db, acc, view="archive") == {"Archived invoice"}
    assert ids(db, acc, view="archive", q="invoice") == {"Archived invoice"}
    assert ids(db, acc, archive=True) == {"Archived invoice"}


def test_is_archived_is_refused_with_a_plain_message(db, layout, make_account):
    acc = make_account()
    with pytest.raises(SearchError, match="Local archive page"):
        mail_conditions(db, acc.id, q="is:archived", now=NOW)


def test_counts_include_archive_and_suggested(db, layout, make_account):
    acc = make_account()
    a, _ = setup(db, layout, acc)
    db.add(
        RuleDecision(
            account_id=acc.id,
            message_id=a.id,
            how=DecisionHow.AI,
            status=DecisionStatus.SUGGESTED,
        )
    )
    db.commit()
    counts = view_counts(db, acc.id)
    assert counts["archive"] == 1 and counts["suggested"] == 1 and counts["all"] == 1
    assert counts["inbox"] == 1
    assert ids(db, acc, view="suggested") == {"Kept invoice"}


def test_backup_keeps_local_archive_in_trash_and_when_gone(db, layout, make_account):
    acc = make_account()
    _, b = setup(db, layout, acc)
    apply_labels(db, acc.id, b.provider_id, ["TRASH"])
    mark_deleted(db, acc.id, b.provider_id)  # Gmail emptied its trash
    db.refresh(b)
    assert b.state == MailState.LOCAL_ARCHIVE


def test_taken_out_of_trash_in_gmail_is_back_in_mail(db, layout, make_account):
    acc = make_account()
    _, b = setup(db, layout, acc)
    apply_labels(db, acc.id, b.provider_id, ["INBOX"])
    db.refresh(b)
    assert b.state == MailState.IN_MAIL


def test_suggested_count_leaves_out_archived_mail(db, layout, make_account):
    acc = make_account()
    a, b = setup(db, layout, acc)
    for m in (a, b):
        db.add(
            RuleDecision(
                account_id=acc.id,
                message_id=m.id,
                how=DecisionHow.AI,
                status=DecisionStatus.SUGGESTED,
            )
        )
    db.commit()
    assert view_counts(db, acc.id)["suggested"] == 1


def test_is_archived_is_accepted_inside_the_archive_view(db, layout, make_account):
    acc = make_account()
    setup(db, layout, acc)
    assert ids(db, acc, view="archive", q="is:archived invoice") == {"Archived invoice"}
    assert ids(db, acc, archive=True, q="is:archived") == {"Archived invoice"}
    with pytest.raises(SearchError):
        mail_conditions(db, acc.id, view="inbox", q="is:archived", now=NOW)


def _label_rows(db, message_id):
    from app.models import MailMessageLabel

    return set(
        db.scalars(
            select(MailMessageLabel.label_id).where(MailMessageLabel.message_id == message_id)
        )
    )


def test_label_table_counts_and_label_views_leave_out_archive(db, layout, make_account):
    """Merge of 0.2.1: label views and counts read the label table, which holds archived
    mail too; neither may show it outside the archive."""
    from app.models import MailLabel
    from app.services.mail.listing import mail_counts

    acc = make_account()
    db.add(MailLabel(account_id=acc.id, provider_label_id="Label_7", name="Bills", type="user"))
    db.commit()
    keep(db, layout, acc, make_mail("Kept bill"), labels=("INBOX", "Label_7", "STARRED"))
    b = keep(db, layout, acc, make_mail("Archived bill"), labels=("INBOX", "Label_7", "STARRED"))
    b.state = MailState.LOCAL_ARCHIVE
    db.commit()
    assert _label_rows(db, b.id) == {"INBOX", "Label_7", "STARRED"}  # the table has it
    views, labels = mail_counts(db, acc.id)
    assert views["inbox"] == 1 and views["starred"] == 1 and views["archive"] == 1
    assert {lab["id"]: lab["count"] for lab in labels}["Label_7"] == 1
    assert ids(db, acc, label="Label_7") == {"Kept bill"}
    assert ids(db, acc, view="starred") == {"Kept bill"}
    assert ids(db, acc, q="label:Bills") == {"Kept bill"}
    assert ids(db, acc, view="archive", label="Label_7") == {"Archived bill"}


def test_only_archived_labels_drop_out_of_the_tally(db, layout, make_account):
    from app.services.mail.listing import _label_tally

    acc = make_account()
    b = keep(db, layout, acc, make_mail("Only archived"), labels=("Label_9",))
    b.state = MailState.LOCAL_ARCHIVE
    db.commit()
    keep(db, layout, acc, make_mail("Kept"), labels=("INBOX",))
    assert _label_tally(db, acc.id) == {"INBOX": 1}


def test_totals_endpoint_leaves_out_archive_unless_archive_view(
    auth_client, db, layout, make_account
):
    acc = make_account()
    a, b = setup(db, layout, acc)

    def totals(**params):
        r = auth_client.get("/api/mail/totals", params={"account_id": acc.id, **params})
        assert r.status_code == 200, r.text
        return r.json()["total"]

    assert totals() == 1
    assert totals(view="inbox") == 1
    assert totals(q="invoice") == 1
    assert totals(view="archive") == 1
    assert totals(view="archive", q="Archived") == 1
    assert totals(view="archive", q="Kept") == 0
    r = auth_client.get("/api/mail/totals", params={"account_id": acc.id, "q": "is:archived"})
    assert r.status_code == 422


def test_paged_list_leaves_out_archive_on_every_page(auth_client, db, layout, make_account):
    """Merge of 0.2.1: the list walks mails newest first and stops at a page; archived mail
    must not fill pages, show up later, or count in a thread."""
    acc = make_account()
    archived = set()
    for i in range(12):
        when = datetime(2026, 10, 1, tzinfo=UTC).replace(hour=i)
        row = keep(db, layout, acc, make_mail(f"Mail {i}"), when=when)
        if i % 3 == 0:
            row.state = MailState.LOCAL_ARCHIVE
            archived.add(row.thread_id)
    # one thread with a kept and an archived mail
    mixed = keep(db, layout, acc, make_mail("Mixed kept"), pid="mixedkept")
    late = keep(db, layout, acc, make_mail("Mixed archived"), pid="mixedarch")
    late.thread_id, late.state = mixed.thread_id, MailState.LOCAL_ARCHIVE
    late.date = datetime(2026, 10, 2, tzinfo=UTC)  # the newest mail of all
    db.commit()
    seen, cursor = [], None
    while True:
        params = {"account_id": acc.id, "limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = auth_client.get("/api/mail/threads", params=params).json()
        seen += page["threads"]
        cursor = page["next_cursor"]
        if not cursor:
            break
    tids = [t["thread_id"] for t in seen]
    assert len(tids) == len(set(tids)) == 9  # 8 kept singles + the mixed thread
    assert not archived & set(tids)
    m = next(t for t in seen if t["thread_id"] == mixed.thread_id)
    assert m["count"] == 1 and m["subject"] == "Mixed kept"
    arch = auth_client.get(
        "/api/mail/threads", params={"account_id": acc.id, "view": "archive", "limit": 50}
    ).json()["threads"]
    assert {t["thread_id"] for t in arch} == archived | {mixed.thread_id}
    thread = auth_client.get(
        f"/api/mail/threads/{mixed.thread_id}", params={"account_id": acc.id}
    ).json()
    assert [x["subject"] for x in thread] == ["Mixed kept"]
    thread = auth_client.get(
        f"/api/mail/threads/{mixed.thread_id}", params={"account_id": acc.id, "archive": True}
    ).json()
    assert [x["subject"] for x in thread] == ["Mixed archived"]
