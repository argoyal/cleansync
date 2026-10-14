"""Allow Gmail filters (0.5.2 spec §5): the Gmail calls, the permission and the switch."""

import asyncio
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from sqlalchemy import select

from app.devtools.fake_gmail import FakeGmail, make_mail
from app.models import Account, Event
from app.models.enums import ServiceKind, ServiceState
from app.services.accounts.filters import LOST_MSG, permission_lost
from app.services.accounts.oauth import (
    DRIVE_SCOPE,
    FULL_MAIL_SCOPE,
    GMAIL_SCOPE,
    SETTINGS_SCOPE,
)
from app.services.accounts.services import ensure_services, set_service_state
from app.services.events.bus import EventBus
from app.services.mail.adapter import FiltersRefused, GmailFilter
from tests.mail_helpers import fake_adapter
from tests.test_mail_setup import _account, _google

BOTH = f"{DRIVE_SCOPE} {GMAIL_SCOPE}"


def _run(coro):
    return asyncio.run(coro)


def test_create_list_delete():
    adapter = fake_adapter(FakeGmail())
    fid = _run(adapter.create_filter("a@x.com"))
    assert fid
    assert _run(adapter.list_filters()) == [GmailFilter(fid, "a@x.com", True)]
    assert _run(adapter.create_filter("a@x.com")) == fid  # the same filter: the exists path
    _run(adapter.delete_filter(fid))
    assert _run(adapter.list_filters()) == []
    _run(adapter.delete_filter(fid))  # already gone: fine


def test_exists_answer_without_a_match_is_an_error():
    from app.services.mail.adapter import MailApiError

    fake = FakeGmail()
    fake.fail("POST", "/settings/filters", 400)  # a 400 that is not "already exists"
    with pytest.raises(MailApiError):
        _run(fake_adapter(fake).create_filter("a@x.com"))


def test_scope_refused_raises_filters_refused():
    fake = FakeGmail(settings_scope=False)
    adapter = fake_adapter(fake)
    for call in (
        lambda: adapter.create_filter("a@x.com"),
        adapter.list_filters,
        lambda: adapter.delete_filter("abc"),
    ):
        with pytest.raises(FiltersRefused) as e:
            _run(call())
        assert str(e.value) == (
            "Google no longer allows CleanSync to change Gmail filters for this mailbox."
        )
        assert e.value.status == 403


def test_new_mail_skips_inbox_after_filter():
    fake = FakeGmail()
    before = fake.add(make_mail("old", sender="a@x.com"))
    _run(fake_adapter(fake).create_filter("a@x.com"))
    fresh = fake.add(make_mail("new", sender="Ann <a@x.com>"))
    other = fake.add(make_mail("other", sender="b@x.com"))
    assert fake.messages[fresh].labels == []
    assert "INBOX" in fake.messages[before].labels  # a filter works on new mail only
    assert "INBOX" in fake.messages[other].labels


def _mail_ready(app, make_account, scopes=BOTH, email="alice@example.com"):
    acc = make_account(email)
    with app.state.session_factory() as db:
        ensure_services(db, acc)
    with app.state.session_factory() as db:
        row = db.get(Account, acc.id)
        row.scopes = scopes
        db.commit()
        set_service_state(db, acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    return acc


def _switch(auth_client, acc, on: bool):
    return auth_client.post(f"/api/accounts/{acc.id}/gmail-filters", json={"on": on})


def _query(url):
    return parse_qs(urlparse(url).query)


def _me(auth_client):
    return auth_client.get("/api/accounts").json()[0]


def test_turn_on_asks_google_for_settings_scope(auth_client, app, make_account):
    acc = _mail_ready(app, make_account)
    r = _switch(auth_client, acc, True)
    assert r.status_code == 200, r.text
    url = r.json()["auth_url"]
    assert "gmail.settings.basic" in url
    scopes = set(_query(url)["scope"][0].split())
    assert {SETTINGS_SCOPE, GMAIL_SCOPE, DRIVE_SCOPE} <= scopes
    assert FULL_MAIL_SCOPE not in scopes
    assert "mail.google.com" not in url


@respx.mock
def test_callback_turns_it_on_or_denied(auth_client, app, make_account):
    acc = _mail_ready(app, make_account)
    q = _query(_switch(auth_client, acc, True).json()["auth_url"])
    _google(f"{BOTH} {SETTINGS_SCOPE}")
    back = auth_client.get(
        f"/api/oauth/callback?state={q['state'][0]}&code=c", follow_redirects=False
    )
    assert back.headers["location"] == f"/?connected={acc.id}&filters=on"
    me = _me(auth_client)
    assert me["gmail_filters"] is True and me["gmail_filters_note"] is None
    assert me["has_photo"] is False


@respx.mock
def test_callback_denied(auth_client, app, make_account):
    acc = _mail_ready(app, make_account)
    q = _query(_switch(auth_client, acc, True).json()["auth_url"])
    _google(BOTH)  # the box was not ticked
    back = auth_client.get(
        f"/api/oauth/callback?state={q['state'][0]}&code=c", follow_redirects=False
    )
    assert back.headers["location"] == f"/?connected={acc.id}&filters=denied"
    assert _me(auth_client)["gmail_filters"] is False


@respx.mock
def test_turn_off_forgets_pending_sign_in(auth_client, app, make_account):
    acc = _mail_ready(app, make_account)
    q = _query(_switch(auth_client, acc, True).json()["auth_url"])
    assert _switch(auth_client, acc, False).json() == {"auth_url": None}
    _google(f"{BOTH} {SETTINGS_SCOPE}")
    back = auth_client.get(
        f"/api/oauth/callback?state={q['state'][0]}&code=c", follow_redirects=False
    )
    assert back.headers["location"].startswith("/?oauth_error=")
    assert _me(auth_client)["gmail_filters"] is False


def test_already_granted_needs_no_sign_in_and_off_keeps_the_scope(auth_client, app, make_account):
    acc = _mail_ready(app, make_account, f"{BOTH} {SETTINGS_SCOPE}")
    assert _switch(auth_client, acc, True).json() == {"auth_url": None}
    assert _me(auth_client)["gmail_filters"] is True
    assert _switch(auth_client, acc, False).json() == {"auth_url": None}
    assert _me(auth_client)["gmail_filters"] is False
    with app.state.session_factory() as db:
        assert SETTINGS_SCOPE in db.get(Account, acc.id).scopes.split()


def test_pretend_account_turns_on_without_google(auth_client, app, settings):
    settings.allow_local_accounts = True
    folder = app.state.layout.root / "pretend"
    acc = asyncio.run(app.state.accounts.add_local_account("bob@example.com", folder))
    assert auth_client.post(f"/api/accounts/{acc.id}/mail/setup").json() == {"auth_url": None}
    r = _switch(auth_client, acc, True)
    assert r.status_code == 200 and r.json() == {"auth_url": None}
    assert _me(auth_client)["gmail_filters"] is True


def test_not_connected_refused(auth_client, app, make_account):
    acc = _account(app, make_account)  # Mail not set up
    r = _switch(auth_client, acc, True)
    assert r.status_code == 400
    assert r.json()["detail"] == "Set up mail backup for this account first."
    assert auth_client.post("/api/accounts/999/gmail-filters", json={"on": True}).status_code == 404


def test_permission_lost_turns_it_off_once(db, make_account):
    acc = make_account()
    acc.scopes, acc.gmail_filters = f"{GMAIL_SCOPE} {SETTINGS_SCOPE}", True
    db.commit()
    bus = EventBus()
    permission_lost(db, bus, acc.id)
    permission_lost(db, bus, acc.id)
    db.refresh(acc)
    assert acc.gmail_filters is False and acc.gmail_filters_note == LOST_MSG
    assert SETTINGS_SCOPE not in acc.scopes.split() and GMAIL_SCOPE in acc.scopes.split()
    events = db.scalars(select(Event).where(Event.kind == "gmail_filters_lost")).all()
    assert [e.message for e in events] == [f"{acc.email}: {LOST_MSG}"]


@respx.mock
def test_plain_sign_in_never_turns_it_on(auth_client, app, make_account):
    acc = _mail_ready(app, make_account, f"{BOTH} {SETTINGS_SCOPE}")
    url = auth_client.post(f"/api/accounts/{acc.id}/reconnect").json()["auth_url"]
    assert SETTINGS_SCOPE not in _query(url)["scope"][0].split()
    _google(f"{BOTH} {SETTINGS_SCOPE}")  # Google still remembers it
    auth_client.get(
        f"/api/oauth/callback?state={_query(url)['state'][0]}&code=c", follow_redirects=False
    )
    assert _me(auth_client)["gmail_filters"] is False
