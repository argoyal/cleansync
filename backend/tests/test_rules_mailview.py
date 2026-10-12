import pytest

from app.devtools.fake_gmail import make_mail
from app.services.mail.store import eml_path
from app.services.rules.mailview import (
    CopyUnreadable,
    domain_of,
    mail_for_model,
    model_text,
    sender_of,
)
from tests.rules_helpers import keep


def test_reads_the_local_copy(db, layout, make_account):
    acc = make_account()
    raw = make_mail(
        "Your weekly digest",
        sender="News Team <News@Letters.Example.com>",
        to="list@example.com",
        cc="alice@example.com",
        text="Top stories https://letters.example.com/a/b?c=1 more",
        headers={
            "List-Unsubscribe": "<https://letters.example.com/u>",
            "List-Id": "<n.example.com>",
        },
        attachments=(("invite.ics", "text/calendar", b"BEGIN:VCALENDAR"),),
    )
    m = mail_for_model(layout, acc.email, keep(db, layout, acc, raw))
    assert m.sender == "news@letters.example.com" and m.sender_name == "News Team"
    assert m.owner_in == "cc" and m.cc == ["alice@example.com"]
    assert m.list_unsubscribe and m.mailing_list and m.calendar_invite
    assert "[link: letters.example.com]" in m.text and "?c=1" not in m.text
    assert m.attachments == ["invite.ics"] and m.labels == ["INBOX"]


def test_quoted_replies_are_cut_and_text_is_capped():
    text = "Thanks, see below.\n\nOn Mon, 1 Sep 2026, Bob <b@example.com> wrote:\n> old stuff\n"
    assert model_text(text) == "Thanks, see below."
    assert model_text("> quoted\nnew line") == "new line"
    assert len(model_text("word " * 2000)) == 2000


def test_a_changed_or_missing_copy_is_refused(db, layout, make_account):
    acc = make_account()
    row = keep(db, layout, acc, make_mail("Hi"))
    path = eml_path(layout, acc.email, row.file_path)
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(CopyUnreadable, match="size changed"):
        mail_for_model(layout, acc.email, row)
    path.unlink()
    with pytest.raises(CopyUnreadable, match="missing"):
        mail_for_model(layout, acc.email, row)


@pytest.mark.parametrize(
    "value,addr", [("A <X@Example.com>", "x@example.com"), ("", ""), ("not an address", "")]
)
def test_sender_of(value, addr):
    assert sender_of(value) == addr


def test_domain_of():
    assert domain_of("a@Mail.Example.com") == "mail.example.com" and domain_of("x") == ""
