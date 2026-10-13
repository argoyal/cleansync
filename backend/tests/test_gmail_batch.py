"""Bulk offload spec §11: batch_delete, batch_trash and delete, on the fake Gmail and in
the shape real Gmail expects."""

import json

import httpx
import pytest
import respx

from app.devtools.fake_gmail import FakeGmail, make_mail
from app.services.google.client import GoogleClient
from app.services.google.quota import UNIT_COSTS, ApiBudget
from app.services.mail.adapter import (
    MailApiError,
    MailAuthError,
    MailNotFound,
    PermanentDeleteRefused,
)
from app.services.mail.gmail import BATCH_MAX, GMAIL, GmailAdapter, StaticTokens
from tests.mail_helpers import fake_adapter


def _mailbox(n=3):
    fake = FakeGmail()
    ids = [fake.add(make_mail(f"m{i}"), labels=("INBOX",)) for i in range(n)]
    return fake, ids


def test_batch_calls_cost_50_units_and_one_delete_10():
    assert UNIT_COSTS["gmail.messages.batchDelete"] == 50
    assert UNIT_COSTS["gmail.messages.batchModify"] == 50
    assert UNIT_COSTS["gmail.messages.delete"] == 10


async def test_batch_delete_removes_mail_for_good_and_ignores_ids_gmail_lacks():
    fake, ids = _mailbox()
    start = fake.history_id
    await fake_adapter(fake).batch_delete([ids[0], ids[1]])
    assert set(fake.messages) == {ids[2]}
    await fake_adapter(fake).batch_delete([ids[0]])  # already gone: Gmail says nothing
    deleted = [h for h in fake.history if int(h["id"]) > start and "messagesDeleted" in h]
    assert len(deleted) == 2  # the keep-up check sees them like mail deleted in Gmail


async def test_batch_trash_moves_the_batch_to_the_trash_in_one_call():
    fake, ids = _mailbox()
    await fake_adapter(fake).batch_trash(ids)
    assert all("TRASH" in fake.messages[i].labels for i in ids)
    assert all("INBOX" not in fake.messages[i].labels for i in ids)
    posts = [c[1].rsplit("/", 1)[-1] for c in fake.calls if c[0] == "POST"]
    assert posts == ["batchModify"]


async def test_batch_trash_with_an_id_gmail_lacks_is_refused_and_changes_nothing():
    fake, ids = _mailbox()
    with pytest.raises(MailApiError) as e:
        await fake_adapter(fake).batch_trash([ids[0], "abc123"])
    assert e.value.status == 400
    assert "TRASH" not in fake.messages[ids[0]].labels


async def test_delete_removes_one_mail_and_says_when_gmail_has_none():
    fake, ids = _mailbox(1)
    await fake_adapter(fake).delete(ids[0])
    assert fake.messages == {}
    with pytest.raises(MailNotFound):
        await fake_adapter(fake).delete(ids[0])


@pytest.mark.parametrize("call", ["batch_delete", "delete"])
async def test_a_missing_full_permission_is_its_own_error_not_a_reconnect(call):
    fake, ids = _mailbox(1)
    fake.fail("POST", r"/batchDelete$", 403, reason="insufficientPermissions")
    fake.fail("DELETE", r"/messages/", 403, reason="insufficientPermissions")
    adapter = fake_adapter(fake)
    with pytest.raises(PermanentDeleteRefused) as e:
        if call == "batch_delete":
            await adapter.batch_delete(ids)
        else:
            await adapter.delete(ids[0])
    assert not isinstance(e.value, MailAuthError) and e.value.status == 403
    assert ids[0] in fake.messages


async def test_a_scope_403_on_batch_trash_stays_a_reconnect_error():
    """Only the permanent-delete calls turn a 403 into "no full permission": Offload to the
    trash needs the ordinary Gmail permission, so a 403 there means reconnect."""
    fake, ids = _mailbox(1)
    fake.fail("POST", r"/batchModify$", 403, reason="insufficientPermissions")
    with pytest.raises(MailAuthError) as e:
        await fake_adapter(fake).batch_trash(ids)
    assert not isinstance(e.value, PermanentDeleteRefused)
    assert "TRASH" not in fake.messages[ids[0]].labels


async def test_a_batch_takes_one_to_1000_good_ids():
    adapter = fake_adapter(FakeGmail())
    with pytest.raises(ValueError):
        await adapter.batch_delete([])
    with pytest.raises(ValueError):
        await adapter.batch_trash(["abc"] * (BATCH_MAX + 1))
    with pytest.raises(ValueError):
        await adapter.batch_delete(["../profile"])


def _real(budget):
    google = GoogleClient(budget=budget, pacer=None, http_factory=lambda: httpx.AsyncClient())
    return GmailAdapter(google, StaticTokens("ya29.test"), "gd_alice")


@respx.mock
async def test_the_requests_have_gmails_shape_and_cost(session_factory):
    budget = ApiBudget(session_factory, budget=lambda: 10_000)
    bd = respx.post(f"{GMAIL}/messages/batchDelete").mock(return_value=httpx.Response(204))
    bm = respx.post(f"{GMAIL}/messages/batchModify").mock(return_value=httpx.Response(204))
    one = respx.delete(f"{GMAIL}/messages/abc").mock(return_value=httpx.Response(204))
    adapter = _real(budget)
    await adapter.batch_delete(["abc", "def"])
    await adapter.batch_trash(["abc"])
    await adapter.delete("abc")
    assert json.loads(bd.calls[0].request.content) == {"ids": ["abc", "def"]}
    assert json.loads(bm.calls[0].request.content) == {
        "ids": ["abc"],
        "addLabelIds": ["TRASH"],
        "removeLabelIds": [],
    }
    assert one.called
    assert bd.calls[0].request.headers["Authorization"] == "Bearer ya29.test"
    assert budget.used_today() == 50 + 50 + 10


@pytest.mark.parametrize("where", ["errors", "details"])
async def test_insufficient_scope_is_seen_whether_the_reason_is_in_errors_or_details(where):
    reason = "ACCESS_TOKEN_SCOPE_INSUFFICIENT"
    error = {"code": 403, "message": "Insufficient Permission", "status": "PERMISSION_DENIED"}
    error["errors" if where == "errors" else "details"] = [{"reason": reason}]
    transport = httpx.MockTransport(lambda req: httpx.Response(403, json={"error": error}))
    google = GoogleClient(
        budget=None, pacer=None, http_factory=lambda: httpx.AsyncClient(transport=transport)
    )
    gmail = GmailAdapter(google, StaticTokens("ya29.fake"), "gd_alice")
    with pytest.raises(PermanentDeleteRefused):
        await gmail.batch_delete(["abc123"])
