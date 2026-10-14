"""Unsubscribe jobs (0.5.2 §4, §5, §6): the ladder, auto-archive, keep and the job loop."""

import logging
import time
from collections import Counter
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.models import Account, Event, UnsubscribeJob, UnsubscribeJobItem, UnsubscribeSender
from app.models.enums import AiTaskName, UnsubscribeJobState
from app.services.accounts.filters import LOST_MSG
from app.services.accounts.oauth import SETTINGS_SCOPE
from app.services.ai.client import AiAnswer, AiPaused
from app.services.ai.connections import AiTarget
from app.services.ai.limits import Pause
from app.services.google.client import RateLimited
from app.services.google.quota import BudgetReached
from app.services.mail.service import MailError
from app.services.unsubscribe.jobs import (
    ALREADY_RUNNING_MSG,
    SETUP_FAILED_MSG,
    STOPPED_MSG,
    UnsubscribeJobs,
)
from app.services.unsubscribe.steps import (
    DONE_FILTER,
    FILTER_CAP_MSG,
    FORM_NOT_SIMPLE_MSG,
    KEEP_NEEDS_FILTERS_MSG,
    MAILTO_ONLY_MSG,
    MODEL_PAUSED_MSG,
    NO_MODEL_MSG,
    NOT_CONFIRMED_MSG,
    Ladder,
)
from app.services.unsubscribe.web import SafeWeb
from tests.offload_helpers import offload_box  # noqa: F401  (a fixture)
from tests.test_unsub_senders import mail

FORM_PAGE = """<html><body><form method="post" action="/done">
<input type="hidden" name="t" value="abc">
<input type="checkbox" name="unsubscribe_all" value="1">
<button type="submit">Unsubscribe</button></form></body></html>"""
CAPTCHA_PAGE = """<html><body><form method="post" action="/done">
<div class="g-recaptcha"></div><button type="submit">Go</button></form></body></html>"""
TARGET = AiTarget(1, "http://model/v1", None, {}, "m", 0.0, 5)
ONE_CLICK = "List-Unsubscribe=One-Click"


class FakeAi:
    def __init__(self, answer="confirmed", paused=False):
        self.answer, self.paused = answer, paused
        self.calls = []

    async def ask(self, target, *, system, user, schema, schema_name, parse_text):
        self.calls.append(user)
        if self.paused:
            raise AiPaused(Pause("limited", None, "Daily limit used up.", None, datetime.now(UTC)))
        return AiAnswer(
            data={"answer": self.answer}, raw="", mode="json_schema", took_ms=1, model="m"
        )


class FakeConnections:
    def __init__(self, target=TARGET):
        self.t = target
        self.asked = []

    def target(self, task):
        self.asked.append(task)
        return self.t


class Box:
    """One mailbox on the fake Gmail, a fake web (by method and path) and a fake model."""

    def __init__(self, app, ob):
        self.app, self.ob, self.acc = app, ob, ob.acc
        self.sf = app.state.session_factory
        self.routes: dict[tuple[str, str], tuple[int, str, str] | object] = {}
        self.calls: Counter = Counter()
        self.ips = {"x.example": ["93.184.216.34"], "inside.example": ["10.0.0.1"]}
        self.ai = FakeAi()
        self.connections = FakeConnections()
        self.web = SafeWeb(resolve=self._resolve, transport=httpx.MockTransport(self._handle))
        self.jobs = UnsubscribeJobs(
            sf=self.sf,
            bus=app.state.bus,
            mail=app.state.mail,
            web=self.web,
            ai=self.ai,
            connections=self.connections,
            clock=lambda: datetime.now(UTC),
        )

    async def _resolve(self, host):
        return self.ips.get(host, ["93.184.216.34"])

    def _handle(self, req: httpx.Request) -> httpx.Response:
        key = (req.method, req.url.path)
        self.calls[key] += 1
        got = self.routes.get(key, (404, "text/plain", "no"))
        if callable(got):
            got = got(req)
        if isinstance(got, httpx.Response):
            return got
        status, ctype, body = got
        return httpx.Response(status, text=body, headers={"content-type": ctype})

    def allow_filters(self, on=True):
        with self.sf() as db:
            acc = db.get(Account, self.acc.id)
            acc.gmail_filters = on
            acc.scopes = f"{acc.scopes} {SETTINGS_SCOPE}".strip()
            db.commit()

    async def run(self, kind, addresses):
        job_id = await self.jobs.start(self.acc.id, kind, addresses)
        await self.jobs.wait(job_id)
        return job_id

    def items(self, job_id):
        with self.sf() as db:
            return list(
                db.scalars(
                    select(UnsubscribeJobItem)
                    .where(UnsubscribeJobItem.job_id == job_id)
                    .order_by(UnsubscribeJobItem.id)
                )
            )

    def item(self, job_id):
        (only,) = self.items(job_id)
        return only

    def job(self, job_id):
        with self.sf() as db:
            return db.get(UnsubscribeJob, job_id)

    def sender(self, address):
        with self.sf() as db:
            return db.scalar(
                select(UnsubscribeSender).where(
                    UnsubscribeSender.account_id == self.acc.id,
                    UnsubscribeSender.address == address,
                )
            )

    def events(self):
        with self.sf() as db:
            return list(db.scalars(select(Event).where(Event.kind == "unsubscribe_job_finished")))


@pytest.fixture
def box(app, offload_box):  # noqa: F811
    return Box(app, offload_box)


def ok(ctype="text/plain", body="ok"):
    return (200, ctype, body)


# ---- the ladder ---------------------------------------------------------------------------


async def test_one_click_success(box, db):
    mail(db, box.acc, "shop@x.com", lu="<https://x.example/u/1>", lup=ONE_CLICK)
    box.routes[("POST", "/u/1")] = ok()
    job_id = await box.run("unsubscribe", ["Shop@X.com"])
    it = box.item(job_id)
    assert (it.address, it.outcome, it.method) == ("shop@x.com", "unsubscribed", "one_click")
    assert it.note == "Unsubscribed (one-click)."
    assert it.link == "https://x.example/u/1"
    s = box.sender("shop@x.com")
    assert (s.status, s.method) == ("unsubscribed", "one_click")
    job = box.job(job_id)
    assert (job.state, job.done, job.total) == ("DONE", 1, 1) and job.finished_at is not None
    assert [e.message for e in box.events()] == ["Unsubscribed from 1 sender"]


async def test_post_fails_get_works(box, db):
    mail(db, box.acc, "shop@x.com", lu="<https://x.example/u/1>", lup="list-unsubscribe=one-click")
    box.routes[("POST", "/u/1")] = (405, "text/plain", "no")
    box.routes[("GET", "/u/1")] = ok()
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert (it.outcome, it.method, it.note) == ("unsubscribed", "link", "Unsubscribed (link).")


async def test_no_post_header_no_post(box, db):
    mail(db, box.acc, "shop@x.com", lu="<https://x.example/u/1>")
    box.routes[("POST", "/u/1")] = ok()
    box.routes[("GET", "/u/1")] = ok()
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert (it.outcome, it.method) == ("unsubscribed", "link")
    assert box.calls[("POST", "/u/1")] == 0 and box.calls[("GET", "/u/1")] == 1


async def test_post_header_without_one_click_no_post(box, db):
    mail(db, box.acc, "shop@x.com", lu="<https://x.example/u/1>", lup="List-Unsubscribe=Other")
    box.routes[("POST", "/u/1")] = ok()
    box.routes[("GET", "/u/1")] = ok()
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert it.method == "link" and box.calls[("POST", "/u/1")] == 0


async def test_redirected_post_is_not_one_click(box, db):
    mail(db, box.acc, "shop@x.com", lu="<https://x.example/u/1>", lup=ONE_CLICK)
    box.routes[("POST", "/u/1")] = httpx.Response(
        302, headers={"location": "https://x.example/landing"}
    )
    box.routes[("GET", "/landing")] = ok()
    box.routes[("GET", "/u/1")] = ok()
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert (it.outcome, it.method) == ("unsubscribed", "link")
    assert box.calls[("GET", "/landing")] == 1  # the POST was turned into a GET
    assert box.calls[("GET", "/u/1")] == 1  # so the link itself is visited (step b)


def _form_box(box, db, reply="You have been unsubscribed."):
    mail(db, box.acc, "shop@x.com", lu="<https://x.example/u/1>")
    box.routes[("POST", "/u/1")] = (405, "text/plain", "no")
    box.routes[("GET", "/u/1")] = ok("text/html; charset=utf-8", FORM_PAGE)
    box.routes[("POST", "/done")] = ok("text/html", f"<p>{reply}</p>")


async def test_form_confirmed(box, db):
    _form_box(box, db)
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert (it.outcome, it.method, it.note) == ("unsubscribed", "form", "Unsubscribed (form).")
    assert box.connections.asked == [AiTaskName.UNSUBSCRIBE]
    assert "You have been unsubscribed." in box.ai.calls[0]
    assert box.sender("shop@x.com").status == "unsubscribed"


async def test_form_not_confirmed(box, db):
    _form_box(box, db, reply="Please try again.")
    box.ai.answer = "not_confirmed"
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert (it.outcome, it.note, it.link) == (
        "not_confirmed",
        NOT_CONFIRMED_MSG,
        "https://x.example/u/1",
    )
    assert box.sender("shop@x.com") is None  # the status is left alone
    assert [e.message for e in box.events()] == ["Unsubscribed from 0 senders, 1 needs you"]


async def test_form_no_model(box, db):
    _form_box(box, db)
    box.connections.t = None
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert (it.outcome, it.note) == ("not_confirmed", NO_MODEL_MSG)
    assert box.ai.calls == []


async def test_model_paused_never_waits(box, db):
    _form_box(box, db)
    box.ai.paused = True
    began = time.monotonic()
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert time.monotonic() - began < 5
    assert (it.outcome, it.note) == ("not_confirmed", MODEL_PAUSED_MSG)


async def test_captcha_form(box, db):
    mail(db, box.acc, "shop@x.com", lu="<https://x.example/u/1>")
    box.routes[("POST", "/u/1")] = (405, "text/plain", "no")
    box.routes[("GET", "/u/1")] = ok("text/html", CAPTCHA_PAGE)
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert (it.outcome, it.note) == ("not_confirmed", FORM_NOT_SIMPLE_MSG)
    assert box.calls[("POST", "/done")] == 0


async def test_private_link_refused_then_next_link(box, db):
    mail(
        db,
        box.acc,
        "shop@x.com",
        lu="<https://inside.example/a>, <https://x.example/b>",
        lup=ONE_CLICK,
    )
    box.routes[("POST", "/a")] = ok()
    box.routes[("POST", "/b")] = ok()
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert (it.outcome, it.link) == ("unsubscribed", "https://x.example/b")
    assert box.calls[("POST", "/a")] == 0


async def test_all_links_fail_says_why(box, db):
    mail(db, box.acc, "shop@x.com", lu="<https://inside.example/a>")
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert it.outcome == "not_confirmed"
    assert it.note == "inside.example points to a private address, so CleanSync won't open it."
    assert it.link == "https://inside.example/a"


async def test_mailto_only_with_filters(box, db):
    box.allow_filters()
    mail(db, box.acc, "shop@x.com", lu="<mailto:leave@x.com?subject=unsubscribe>")
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert (it.outcome, it.method, it.note) == ("auto_archived", "filter", DONE_FILTER)
    (f,) = box.ob.fake.filters.values()
    assert f["criteria"] == {"from": "shop@x.com"}
    s = box.sender("shop@x.com")
    assert (s.status, s.method, s.filter_id) == ("auto_archived", "filter", f["id"])


async def test_mailto_only_without_filters(box, db):
    mail(db, box.acc, "shop@x.com", lu="<mailto:leave@x.com?subject=unsubscribe>")
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert (it.outcome, it.note) == ("needs_you", MAILTO_ONLY_MSG)
    assert it.link == "mailto:leave@x.com?subject=unsubscribe"
    assert box.ob.fake.filters == {}
    assert box.sender("shop@x.com") is None


async def test_no_link_at_all(box, db):
    mail(db, box.acc, "shop@x.com", lu="<ftp://x.example/u>")
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert (it.outcome, it.note) == (
        "not_confirmed",
        "This sender's mail has no unsubscribe link CleanSync can use.",
    )
    unknown = box.item(await box.run("unsubscribe", ["nobody@x.com"]))
    assert unknown.note == "This sender's mail has no unsubscribe link CleanSync can use."


async def test_body_link_used_when_header_has_only_mailto_and_filters_off(box, db, app):
    m = mail(db, box.acc, "shop@x.com", lu="<mailto:leave@x.com>")
    eml = (
        b"From: Shop <shop@x.com>\r\nTo: alice@example.com\r\nSubject: hi\r\n"
        b"MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=b\r\n\r\n"
        b"--b\r\nContent-Type: text/plain\r\n\r\nhello\r\n"
        b"--b\r\nContent-Type: text/html\r\n\r\n"
        b'<p>hello</p><a href="https://x.example/body-unsub?id=7">Unsubscribe</a>\r\n--b--\r\n'
    )
    folder = app.state.layout.mail_dir(box.acc.email)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "body.eml").write_bytes(eml)
    m.file_path = "body.eml"
    db.commit()
    m.list_unsubscribe_post = ONE_CLICK  # it speaks of the header's link, not the body's
    db.commit()
    box.routes[("POST", "/body-unsub")] = ok()
    box.routes[("GET", "/body-unsub")] = ok()
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert (it.outcome, it.method) == ("unsubscribed", "link")
    assert it.link == "https://x.example/body-unsub?id=7"
    assert box.calls[("POST", "/body-unsub")] == 0  # a body link never gets the POST
    assert box.calls[("GET", "/body-unsub")] == 1


# ---- Gmail filters ------------------------------------------------------------------------


async def test_filter_cap(box):
    box.allow_filters()
    for i in range(990):
        box.ob.fake.filters[f"F{i}"] = {
            "id": f"F{i}",
            "criteria": {"from": f"{i}@y.com"},
            "action": {},
        }
    it = box.item(await box.run("auto_archive", ["shop@x.com"]))
    assert (it.outcome, it.note) == ("needs_you", FILTER_CAP_MSG.format(n=990))
    assert (
        it.note
        == "Gmail allows 1,000 filters and this mailbox has 990. Remove some in Gmail first."
    )
    assert len(box.ob.fake.filters) == 990


async def test_auto_archive_job_saves_filter(box):
    box.allow_filters()
    job_id = await box.run("auto_archive", ["a@x.com", "b@x.com"])
    assert [i.outcome for i in box.items(job_id)] == ["auto_archived", "auto_archived"]
    assert len(box.ob.fake.filters) == 2
    assert box.sender("a@x.com").filter_id in box.ob.fake.filters
    assert [e.message for e in box.events()] == ["Auto-archived 2 senders"]


async def test_filters_refused_turns_permission_off(box):
    box.allow_filters()
    box.ob.fake.settings_scope = False
    it = box.item(await box.run("auto_archive", ["shop@x.com"]))
    assert (it.outcome, it.note) == ("needs_you", LOST_MSG)
    with box.sf() as db:
        acc = db.get(Account, box.acc.id)
        assert acc.gmail_filters is False and acc.gmail_filters_note == LOST_MSG
        assert SETTINGS_SCOPE not in acc.scopes


async def test_keep_removes_our_filter(box):
    box.allow_filters()
    await box.run("auto_archive", ["shop@x.com"])
    assert len(box.ob.fake.filters) == 1
    job_id = await box.run("keep", ["shop@x.com"])
    it = box.item(job_id)
    assert (it.outcome, it.note) == ("kept", "Kept. CleanSync won't suggest this sender again.")
    assert box.ob.fake.filters == {}
    s = box.sender("shop@x.com")
    assert (s.status, s.filter_id) == ("kept", None)
    assert box.events()[-1].message == "Kept 1 sender"


async def test_keep_without_filter_needs_no_permission(box):
    it = box.item(await box.run("keep", ["shop@x.com"]))
    assert it.outcome == "kept"
    assert box.sender("shop@x.com").status == "kept"


async def test_keep_without_permission_needs_you(box):
    box.allow_filters()
    await box.run("auto_archive", ["shop@x.com"])
    box.allow_filters(False)
    it = box.item(await box.run("keep", ["shop@x.com"]))
    assert (it.outcome, it.note) == ("needs_you", KEEP_NEEDS_FILTERS_MSG)
    s = box.sender("shop@x.com")
    assert s.status == "auto_archived" and s.filter_id is not None
    assert len(box.ob.fake.filters) == 1


# ---- the job ------------------------------------------------------------------------------


def _job(box, state, addresses, done_first=False):
    with box.sf() as db:
        job = UnsubscribeJob(
            account_id=box.acc.id,
            kind="unsubscribe",
            state=state,
            total=len(addresses),
            done=1 if done_first else 0,
        )
        db.add(job)
        db.flush()
        for n, a in enumerate(addresses):
            outcome = "unsubscribed" if done_first and n == 0 else None
            db.add(UnsubscribeJobItem(job_id=job.id, address=a, outcome=outcome))
        db.commit()
        return job.id


async def test_one_job_per_mailbox(box):
    _job(box, UnsubscribeJobState.WAITING, ["a@x.com"])
    with pytest.raises(MailError) as e:
        await box.jobs.start(box.acc.id, "unsubscribe", ["b@x.com"])
    assert (str(e.value), e.value.status) == (ALREADY_RUNNING_MSG, 409)
    assert ALREADY_RUNNING_MSG == "An unsubscribe is already running for this mailbox."


async def test_addresses_cleaned_and_empty_refused(box):
    with pytest.raises(MailError) as e:
        await box.jobs.start(box.acc.id, "unsubscribe", [" ", ""])
    assert (str(e.value), e.value.status) == ("Pick at least one sender.", 400)
    job_id = await box.run("keep", ["A@x.com", "a@x.com ", "b@x.com"])
    assert [i.address for i in box.items(job_id)] == ["a@x.com", "b@x.com"]
    assert box.job(job_id).total == 2


async def test_stop(box, db):
    for n in (1, 2, 3):
        mail(db, box.acc, f"s{n}@x.com", lu=f"<https://x.example/u/{n}>", lup=ONE_CLICK)

    job_ids = []

    def first(req):
        box.jobs.stop(job_ids[0])
        return ok()

    box.routes[("POST", "/u/1")] = first
    box.routes[("POST", "/u/2")] = ok()
    box.routes[("POST", "/u/3")] = ok()
    job_id = await box.jobs.start(box.acc.id, "unsubscribe", ["s1@x.com", "s2@x.com", "s3@x.com"])
    job_ids.append(job_id)
    await box.jobs.wait(job_id)
    job = box.job(job_id)
    assert (job.state, job.done, job.reason) == ("STOPPED", 1, STOPPED_MSG)
    assert [i.outcome for i in box.items(job_id)] == ["unsubscribed", None, None]
    assert box.calls[("POST", "/u/2")] == 0


async def test_stop_a_waiting_job(box):
    job_id = _job(box, UnsubscribeJobState.WAITING, ["a@x.com"])
    box.jobs.stop(job_id)
    job = box.job(job_id)
    assert (job.state, job.reason) == ("STOPPED", STOPPED_MSG) and job.finished_at is not None


async def test_recover_and_resume(box, db):
    for n in (1, 2, 3):
        mail(db, box.acc, f"s{n}@x.com", lu=f"<https://x.example/u/{n}>", lup=ONE_CLICK)
        box.routes[("POST", f"/u/{n}")] = ok()
    job_id = _job(
        box, UnsubscribeJobState.RUNNING, ["s1@x.com", "s2@x.com", "s3@x.com"], done_first=True
    )
    box.jobs.recover()
    assert box.job(job_id).state == "WAITING"
    box.jobs.tick()
    box.jobs.tick()  # a second tick doesn't start it twice
    await box.jobs.wait(job_id)
    assert box.calls[("POST", "/u/1")] == 0
    assert box.calls[("POST", "/u/2")] == 1 and box.calls[("POST", "/u/3")] == 1
    job = box.job(job_id)
    assert (job.state, job.done) == ("DONE", 3)


async def test_unexpected_error_goes_on(box, db, monkeypatch):
    mail(db, box.acc, "s1@x.com", lu="<https://x.example/u/1>")
    mail(db, box.acc, "s2@x.com", lu="<https://x.example/u/2>", lup=ONE_CLICK)
    box.routes[("POST", "/u/2")] = ok()
    real = Ladder.unsubscribe

    async def first_breaks(self, sender, *, filters):
        if sender.address == "s1@x.com":
            raise ZeroDivisionError
        return await real(self, sender, filters=filters)

    monkeypatch.setattr(Ladder, "unsubscribe", first_breaks)
    first, second = box.items(await box.run("unsubscribe", ["s1@x.com", "s2@x.com"]))
    assert (first.outcome, first.note) == (
        "not_confirmed",
        "Something went wrong: ZeroDivisionError",
    )
    assert second.outcome == "unsubscribed"


async def test_no_secret_in_logs_or_events(box, db, caplog):
    caplog.set_level(logging.DEBUG)
    mail(db, box.acc, "shop@x.com", lu="<https://x.example/u/1?token=SECRET>")
    box.routes[("POST", "/u/1")] = (500, "text/plain", "no")
    box.routes[("GET", "/u/1")] = (500, "text/plain", "no")
    it = box.item(await box.run("unsubscribe", ["shop@x.com"]))
    assert it.outcome == "not_confirmed"
    assert "x.example" in it.note and "SECRET" not in it.note
    assert it.link == "https://x.example/u/1?token=SECRET"  # the owner's Open page needs it
    assert "SECRET" not in caplog.text
    assert all("SECRET" not in (e.message + str(e.data)) for e in box.events())


# ---- the API ------------------------------------------------------------------------------


def test_api_pick_all_and_errors(auth_client, app, box, db):
    app.state.unsubscribe_jobs = box.jobs
    for a in ("a@x.com", "b@x.com", "c@x.com"):
        mail(db, box.acc, a, lu=f"<https://x.example/{a}>")
        box.routes[("POST", f"/{a}")] = ok()
    url = "/api/mail/unsubscribe/jobs"
    both = {
        "account_id": box.acc.id,
        "kind": "keep",
        "addresses": ["a@x.com"],
        "all": {"tab": "all", "q": ""},
    }
    r = auth_client.post(url, json=both)
    assert (r.status_code, r.json()["detail"]) == (400, "Pick senders, or Pick all, not both.")
    r = auth_client.post(url, json={"account_id": box.acc.id, "kind": "keep"})
    assert (r.status_code, r.json()["detail"]) == (400, "Pick senders, or Pick all, not both.")
    r = auth_client.post(url, json={"account_id": box.acc.id, "kind": "keep", "addresses": []})
    assert (r.status_code, r.json()["detail"]) == (400, "Pick at least one sender.")
    r = auth_client.post(
        url, json={"account_id": box.acc.id, "kind": "keep", "all": {"tab": "kept", "q": ""}}
    )
    assert (r.status_code, r.json()["detail"]) == (400, "Pick at least one sender.")

    r = auth_client.post(
        url, json={"account_id": box.acc.id, "kind": "keep", "all": {"tab": "all", "q": ""}}
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    got = auth_client.get(f"{url}/{job_id}").json()
    assert got["id"] == job_id and got["kind"] == "keep" and got["total"] == 3
    assert [i["address"] for i in got["items"]] == ["a@x.com", "b@x.com", "c@x.com"]
    assert set(got["items"][0]) == {"address", "outcome", "method", "note", "link"}
    latest = auth_client.get(url, params={"account_id": box.acc.id}).json()
    assert latest["id"] == job_id
    assert auth_client.get(url, params={"account_id": 999}).status_code == 404
    assert auth_client.get(f"{url}/999").status_code == 404
    assert auth_client.post(f"{url}/{job_id}/stop").status_code == 200


def test_api_latest_none_and_409(auth_client, app, box):
    app.state.unsubscribe_jobs = box.jobs
    url = "/api/mail/unsubscribe/jobs"
    assert auth_client.get(url, params={"account_id": box.acc.id}).json() is None
    _job(box, UnsubscribeJobState.RUNNING, ["a@x.com"])
    r = auth_client.post(
        url, json={"account_id": box.acc.id, "kind": "keep", "addresses": ["b@x.com"]}
    )
    assert (r.status_code, r.json()["detail"]) == (409, ALREADY_RUNNING_MSG)
    r = auth_client.post(
        url, json={"account_id": box.acc.id, "kind": "nope", "addresses": ["b@x.com"]}
    )
    assert r.status_code == 422


def test_app_wiring(app):
    jobs = app.state.unsubscribe_jobs
    assert jobs.tick in app.state.mail.tick_hooks
    assert app.state.unsubscribe_web.allow_private is False


# ---- the job ends cleanly (final review) --------------------------------------------------


async def test_adapter_trouble_ends_the_job_and_a_new_one_starts(box, monkeypatch):
    def no_adapter(acc):
        raise LookupError("no mail adapter for provider 'imap'")

    monkeypatch.setattr(box.jobs.mail, "adapter_for", no_adapter)
    job_id = await box.run("keep", ["a@x.com"])
    job = box.job(job_id)
    assert (job.state, job.reason) == ("FAILED", SETUP_FAILED_MSG)
    assert SETUP_FAILED_MSG == "CleanSync couldn't open this mailbox. Try again later."
    assert job.finished_at is not None
    assert box.item(job_id).outcome is None
    monkeypatch.undo()
    again = await box.run("keep", ["a@x.com"])  # not refused with 409
    assert box.job(again).state == "DONE"


async def test_finish_trouble_still_ends_the_job(box, monkeypatch):
    import app.services.unsubscribe.jobs as jobs_mod

    def broken(*a, **kw):
        raise RuntimeError("events table locked")

    monkeypatch.setattr(jobs_mod, "record_event", broken)
    job_id = await box.run("keep", ["a@x.com"])
    assert box.job(job_id).state == "DONE"


@pytest.mark.parametrize("which", ["budget", "rate"])
async def test_google_limits_end_the_job(box, db, monkeypatch, which):
    for n in (1, 2):
        mail(db, box.acc, f"s{n}@x.com", lu=f"<https://x.example/u/{n}>")
    err = BudgetReached(10000) if which == "budget" else RateLimited()

    async def limited(self, sender, *, filters):
        raise err

    monkeypatch.setattr(Ladder, "unsubscribe", limited)
    job_id = await box.run("unsubscribe", ["s1@x.com", "s2@x.com"])
    job = box.job(job_id)
    assert (job.state, job.reason) == ("FAILED", str(err))
    assert job.reason.startswith("Paused:" if which == "budget" else "Google asked")
    assert [i.outcome for i in box.items(job_id)] == [None, None]
