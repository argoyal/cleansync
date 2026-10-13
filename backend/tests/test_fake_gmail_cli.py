"""The fake Gmail can be changed from outside, as the owner would in Gmail (the browser test
deletes a mail this way to see it under Deleted in Gmail)."""

import httpx

from app.devtools.fake_gmail import FakeGmail, main, make_mail
from app.services.accounts.service import PRETEND_PREFIX
from app.services.google.client import GoogleClient
from app.services.mail.adapters import MailAdapters


def test_delete_by_subject_records_it_in_the_history(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIVESYNC_ALLOW_LOCAL_ACCOUNTS", "1")
    path = tmp_path / "alice.json"
    fake = FakeGmail(path=path)
    keep = fake.add(make_mail("Keep me"))
    fake.add(make_mail("Remove me"))
    start = fake.history_id
    assert not fake.changed_on_disk()
    assert main(["delete", "--file", str(path), "--subject", "Remove me"]) == 0
    again = FakeGmail.load(path)
    assert list(again.messages) == [keep]
    assert any("messagesDeleted" in h and int(h["id"]) > start for h in again.history)
    assert fake.changed_on_disk()
    assert main(["delete", "--file", str(path), "--subject", "Not there"]) == 1


def test_the_command_line_refuses_unless_pretend_accounts_are_allowed(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "alice.json"
    fake = FakeGmail(path=path)
    keep = fake.add(make_mail("Remove me"))
    monkeypatch.delenv("DRIVESYNC_ALLOW_LOCAL_ACCOUNTS", raising=False)
    assert main(["delete", "--file", str(path), "--subject", "Remove me"]) == 2
    assert "DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1" in capsys.readouterr().err
    assert list(FakeGmail.load(path).messages) == [keep]  # nothing was deleted


def test_the_pretend_mailbox_is_read_again_after_it_changed_on_disk(
    layout, make_account, monkeypatch
):
    monkeypatch.setenv("DRIVESYNC_ALLOW_LOCAL_ACCOUNTS", "1")
    acc = make_account(remote_name=f"{PRETEND_PREFIX}alice")
    adapters = MailAdapters(
        google=GoogleClient(budget=None, pacer=None, http_factory=lambda: httpx.AsyncClient()),
        tokens=None,
        layout=layout,
        allow_pretend=True,
    )
    path = adapters.fake_path(acc.email)
    FakeGmail(email=acc.email, path=path).add(make_mail("Remove me"))
    adapters.for_account(acc)
    first = adapters._fakes[acc.email]
    first.add(make_mail("Saved by the server"))  # the server's own saves don't count
    adapters.for_account(acc)
    assert adapters._fakes[acc.email] is first
    assert main(["delete", "--file", str(path), "--subject", "Remove me"]) == 0
    adapters.for_account(acc)
    now = adapters._fakes[acc.email]
    assert now is not first and len(now.messages) == 1
