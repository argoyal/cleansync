"""The pretend unsubscribe site (browser test only): refuses without the dev switch, records
a one-click POST and lists it."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from app.devtools.fake_gmail import FakeGmail, unsubscribe_mailbox
from app.devtools.fake_unsub_site import FakeUnsubSite, main


def test_refuses_unless_pretend_accounts_are_allowed(monkeypatch, capsys):
    monkeypatch.delenv("DRIVESYNC_ALLOW_LOCAL_ACCOUNTS", raising=False)
    assert main(["--port", "0"]) == 2
    assert "DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1" in capsys.readouterr().err


def test_records_a_one_click_post_and_lists_it():
    site = FakeUnsubSite(("127.0.0.1", 0))
    threading.Thread(target=site.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{site.server_address[1]}"
    try:
        assert json.load(urllib.request.urlopen(f"{base}/hits")) == []
        req = urllib.request.Request(
            f"{base}/one-click", data=b"List-Unsubscribe=One-Click", method="POST"
        )
        assert urllib.request.urlopen(req).read() == b"ok"
        hits = json.load(urllib.request.urlopen(f"{base}/hits"))
        assert [(h["method"], h["path"]) for h in hits] == [("POST", "/one-click")]
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}/nowhere")
        assert e.value.code == 404
    finally:
        site.shutdown()
        site.server_close()


def test_the_unsubscribe_mailbox_has_two_senders_with_three_unread_mails_each():
    from datetime import UTC, datetime

    fake = FakeGmail(email="carol@example.com")
    unsubscribe_mailbox(fake, datetime(2026, 10, 14, tzinfo=UTC))
    assert len(fake.messages) == 6
    assert all("UNREAD" in m.labels and "INBOX" in m.labels for m in fake.messages.values())
