from datetime import UTC, datetime

import httpx
import pytest

from app.devtools.fake_gmail import FakeGmail, make_mail
from app.services.drive_api.tokens import DriveApiError
from app.services.google.client import GoogleClient
from app.services.mail.adapter import (
    GONE,
    IN_INBOX,
    IN_TRASH,
    TOO_OLD,
    MailApiError,
    MailAuthError,
    MailNotFound,
    MailUnreachable,
)
from app.services.mail.gmail import API_OFF_MSG, GmailAdapter, StaticTokens, api_turned_off
from tests.mail_helpers import fake_adapter

WHEN = datetime(2025, 3, 4, 10, 30, tzinfo=UTC)


def _fake():
    fake = FakeGmail()
    ids = [
        fake.add(make_mail(f"Mail {i}", when=WHEN), labels=("INBOX",), when=WHEN) for i in range(5)
    ]
    fake.add(make_mail("Spam"), labels=("SPAM",))
    fake.add(make_mail("Trash"), labels=("TRASH",))
    fake.add(make_mail("Draft"), labels=("DRAFT",))
    return fake, ids


async def test_listing_pages_through_every_mail_but_spam_trash_and_drafts():
    fake, ids = _fake()
    gmail = fake_adapter(fake, page_size=2)
    seen, cursor = [], None
    while True:
        page, cursor = await gmail.list_page(cursor)
        assert len(page) <= 2
        seen += page
        if cursor is None:
            break
    assert sorted(seen) == sorted(ids)
    q = [c for c in fake.calls if c[1].endswith("/messages")][0]
    assert "-in:spam -in:trash -in:drafts" in q[2]["q"]
    assert q[2]["includeSpamTrash"] == "false"


async def test_fetch_raw_gives_the_original_mail_and_its_details():
    fake, ids = _fake()
    gmail = fake_adapter(fake)
    raw, meta = await gmail.fetch_raw(ids[0])
    assert raw == fake.messages[ids[0]].raw
    assert meta.id == ids[0] and meta.labels == ("INBOX",)
    assert meta.internal_date == WHEN
    assert meta.size == len(raw) and meta.history_id is not None
    profile = await gmail.profile()
    assert profile.email == "alice@example.com" and profile.total == 8


async def test_changes_since_collects_adds_label_changes_and_deletes():
    fake, ids = _fake()
    gmail = fake_adapter(fake)
    start = (await gmail.profile()).history_id
    new = fake.add(make_mail("New one"), labels=("INBOX", "UNREAD"))
    fake.set_labels(ids[0], ("INBOX", "STARRED"))
    fake.remove(ids[1])
    changes = await gmail.changes_since(start)
    assert changes.added == {new: ("INBOX", "UNREAD")}
    assert changes.labels == {ids[0]: ("INBOX", "STARRED")}
    assert changes.deleted == {ids[1]}
    assert int(changes.history_id) > int(start)
    fake.forget_history()
    assert await gmail.changes_since(start) is TOO_OLD


async def test_trash_untrash_state_delete_and_import():
    fake, ids = _fake()
    gmail = fake_adapter(fake)
    await gmail.trash(ids[0])
    assert (await gmail.state(ids[0])).where == IN_TRASH
    await gmail.untrash(ids[0])
    st = await gmail.state(ids[0])
    assert st.where == IN_INBOX and st.size == len(fake.messages[ids[0]].raw)
    raw = fake.messages[ids[0]].raw
    await gmail.trash(ids[0])
    await gmail.delete_forever(ids[0])
    assert (await gmail.state(ids[0])).where == GONE
    new_id = await gmail.import_raw(raw, ["INBOX", "Label_1"], WHEN)
    assert new_id != ids[0]
    assert fake.messages[new_id].raw == raw
    assert fake.messages[new_id].labels == ["INBOX", "Label_1"]
    assert fake.messages[new_id].internal_date_ms == int(WHEN.timestamp() * 1000)  # from Date


def _big_mail(mb: int = 6) -> bytes:
    data = b"x" * (mb * 1024 * 1024)
    return make_mail(
        "Big one", when=WHEN, attachments=(("big.bin", "application/octet-stream", data),)
    )


def _import_calls(fake):
    return [c for c in fake.calls if c[1].endswith("/messages/import")]


async def test_a_small_mail_is_imported_in_one_multipart_call():
    fake, ids = _fake()
    gmail = fake_adapter(fake)
    new_id = await gmail.import_raw(make_mail("Small", when=WHEN), ["INBOX"], WHEN)
    calls = _import_calls(fake)
    assert [(c[0], c[2]["uploadType"]) for c in calls] == [("POST", "multipart")]
    assert fake.messages[new_id].labels == ["INBOX"]


async def test_a_mail_over_4_mb_is_imported_with_a_resumable_upload():
    fake, ids = _fake()
    gmail = fake_adapter(fake)
    raw = _big_mail(6)
    assert len(raw) > 6 * 1024 * 1024  # over Gmail's 5 MB limit for a multipart upload
    new_id = await gmail.import_raw(raw, ["INBOX", "Label_1"], WHEN)
    calls = _import_calls(fake)
    assert [(c[0], c[2]["uploadType"]) for c in calls] == [
        ("POST", "resumable"),
        ("PUT", "resumable"),
    ]
    assert "upload_id" in calls[1][2]
    msg = fake.messages[new_id]
    assert msg.raw == raw and msg.labels == ["INBOX", "Label_1"]
    assert msg.internal_date_ms == int(WHEN.timestamp() * 1000)


async def test_the_fake_refuses_a_multipart_upload_over_5_mb_like_gmail():
    fake, ids = _fake()
    resp = fake.handle(
        httpx.Request(
            "POST",
            "https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/import",
            params={"uploadType": "multipart"},
            headers={"Authorization": "Bearer x", "Content-Type": "multipart/related; boundary=b"},
            content=b"--b\r\n\r\n{}\r\n--b\r\n\r\n" + _big_mail(6) + b"\r\n--b--\r\n",
        )
    )
    assert resp.status_code == 413


async def test_a_resumable_upload_is_only_sent_to_google():
    fake, ids = _fake()
    real = fake.handle

    def elsewhere(request):
        resp = real(request)
        if "location" in resp.headers:
            resp.headers["location"] = "https://evil.example.com/upload?upload_id=1"
        return resp

    fake.handle = elsewhere
    gmail = fake_adapter(fake)
    with pytest.raises(MailApiError):
        await gmail.import_raw(_big_mail(5), ["INBOX"], WHEN)
    assert not any(c[0] == "PUT" for c in fake.calls)


async def test_an_expired_token_is_renewed_once_then_reconnect_is_asked_for():
    fake, ids = _fake()
    gmail = fake_adapter(fake)
    fake.fail("GET", r"/messages/", 401, times=1)
    assert (await gmail.state(ids[0])).where == IN_INBOX  # one 401: a fresh token, then fine
    fake.revoke()
    with pytest.raises(MailAuthError) as e:
        await gmail.state(ids[0])
    assert e.value.login is True


async def test_a_missing_mail_permission_asks_to_reconnect_mail_only():
    fake, ids = _fake()
    gmail = fake_adapter(fake)
    fake.fail("GET", r"/profile", 403, reason="insufficientPermissions")
    with pytest.raises(MailAuthError) as e:
        await gmail.profile()
    assert e.value.login is False


async def test_a_rate_limit_is_waited_out():
    fake, ids = _fake()
    waits = []

    async def sleep(s):
        waits.append(s)

    gmail = fake_adapter(fake, sleep=sleep)
    fake.fail("GET", r"/messages/", 429, times=2)
    raw, _ = await gmail.fetch_raw(ids[0])
    assert raw and waits == [1.0, 2.0]


async def test_unknown_mail_and_strange_ids():
    fake, _ = _fake()
    gmail = fake_adapter(fake)
    with pytest.raises(MailNotFound):
        await gmail.fetch_raw("ffffffffffffffff")
    with pytest.raises(ValueError):
        await gmail.fetch_raw("../../etc/passwd")


async def test_the_gmail_api_turned_off_is_its_own_plain_message():
    fake, _ = _fake()
    gmail = fake_adapter(fake)
    fake.fail("GET", r"/profile", 403, reason="accessNotConfigured")
    with pytest.raises(MailApiError) as e:
        await gmail.profile()
    assert not isinstance(e.value, MailAuthError)
    assert str(e.value) == API_OFF_MSG
    assert "Gmail API is turned off" in API_OFF_MSG


def test_service_disabled_in_the_error_details_counts_as_turned_off():
    body = {
        "error": {
            "code": 403,
            "status": "PERMISSION_DENIED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED"}
            ],
        }
    }
    assert api_turned_off(httpx.Response(403, json=body))
    assert not api_turned_off(httpx.Response(403, json={"error": {"errors": []}}))
    assert not api_turned_off(httpx.Response(403, text="not json"))


async def test_server_trouble_is_told_apart_from_a_refused_mail():
    fake, ids = _fake()
    gmail = fake_adapter(fake)
    fake.fail("GET", r"/messages/", 503, times=1)
    with pytest.raises(MailUnreachable):
        await gmail.fetch_raw(ids[0])
    fake.fail("GET", r"/messages/", 400, times=1)
    with pytest.raises(MailApiError) as e:
        await gmail.fetch_raw(ids[0])
    assert not isinstance(e.value, MailUnreachable) and e.value.status == 400


async def test_no_network_or_no_token_is_trouble_reaching_gmail():
    def down(request):
        raise httpx.ConnectError("no network")

    google = GoogleClient(
        budget=None,
        pacer=None,
        http_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(down)),
    )
    with pytest.raises(MailUnreachable):
        await GmailAdapter(google, StaticTokens("t"), "gd_alice").labels()

    class BrokenTokens(StaticTokens):
        async def access_token(self, remote_name):
            raise DriveApiError("could not read the saved login")

    fake, _ = _fake()
    google = GoogleClient(
        budget=None, pacer=None, http_factory=lambda: httpx.AsyncClient(transport=fake.transport())
    )
    with pytest.raises(MailUnreachable):
        await GmailAdapter(google, BrokenTokens("t"), "gd_alice").labels()
