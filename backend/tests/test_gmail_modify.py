import pytest

from app.devtools.fake_gmail import FakeGmail, make_mail
from app.services.mail.adapter import MailApiError, MailNotFound
from tests.mail_helpers import fake_adapter


async def test_modify_adds_and_removes_labels_and_records_history():
    fake = FakeGmail()
    mid = fake.add(make_mail("Hi"), labels=("INBOX", "UNREAD"))
    start = fake.history_id
    labels = await fake_adapter(fake).modify(mid, ["STARRED"], ["INBOX", "UNREAD"])
    assert set(labels) == {"STARRED"} and set(fake.messages[mid].labels) == {"STARRED"}
    assert fake.history_id > start  # the backup sees the change like any other


async def test_modify_unknown_mail_is_not_found():
    with pytest.raises(MailNotFound):
        await fake_adapter(FakeGmail()).modify("nope", [], ["INBOX"])


async def test_create_label_and_duplicate_name():
    fake = FakeGmail()
    lab = await fake_adapter(fake).create_label("Newsletters")
    assert (
        lab.name == "Newsletters"
        and lab.type == "user"
        and fake.user_labels[lab.id] == "Newsletters"
    )
    with pytest.raises(MailApiError) as e:
        await fake_adapter(fake).create_label("newsletters")
    assert e.value.status == 409
