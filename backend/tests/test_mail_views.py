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
    with pytest.raises(
        SearchError, match=r"^Mail deleted in Gmail is in the Deleted in Gmail view\.$"
    ):
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
