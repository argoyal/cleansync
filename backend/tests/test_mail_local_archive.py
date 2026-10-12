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
