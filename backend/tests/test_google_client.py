import httpx
import pytest
import respx

from app.models.enums import JobKind, JobState, Trigger
from app.services.drive_api.client import FILES_URL
from app.services.google.client import RATE_MSG, GoogleClient, RateLimited
from app.services.google.quota import ApiBudget, BudgetReached

URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"


class Recorder:
    def __init__(self):
        self.waits: list[tuple[str, int]] = []

    async def wait(self, key, units):
        self.waits.append((key, units))


def _client(session_factory, budget=10_000, **kw):
    sleeps: list[float] = []

    async def sleep(s):
        sleeps.append(s)

    b = ApiBudget(session_factory, budget=lambda: budget)
    pacer = Recorder()
    return GoogleClient(budget=b, pacer=pacer, sleep=sleep, **kw), b, pacer, sleeps


@respx.mock
async def test_each_call_is_paced_per_mailbox_and_counted(session_factory):
    respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    client, budget, pacer, _ = _client(session_factory)
    resp = await client.request("gd_alice", "gmail.profile", "GET", URL)
    assert resp.status_code == 200
    assert pacer.waits == [("gd_alice", 1)]
    assert budget.used_today() == 1


@respx.mock
async def test_rate_limits_are_waited_out_and_never_fail_the_run(session_factory):
    limited = httpx.Response(
        403, json={"error": {"errors": [{"reason": "userRateLimitExceeded"}], "code": 403}}
    )
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, json={}),
            limited,
            httpx.Response(429, headers={"Retry-After": "7"}, json={}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client, budget, _, sleeps = _client(session_factory)
    resp = await client.request("gd_alice", "gmail.profile", "GET", URL)
    assert resp.json() == {"ok": True}
    assert route.call_count == 4
    assert sleeps == [1.0, 2.0, 7.0]  # growing waits; Google's own Retry-After is followed
    assert budget.used_today() == 4  # every try is counted


@respx.mock
async def test_after_many_rate_limits_it_says_so_instead_of_failing(session_factory):
    respx.get(URL).mock(return_value=httpx.Response(429, json={}))
    client, _, _, sleeps = _client(session_factory, rate_tries=3)
    with pytest.raises(RateLimited, match="slow down"):
        await client.request("gd_alice", "gmail.profile", "GET", URL)
    assert RATE_MSG.startswith("Google asked CleanSync to slow down")
    assert sleeps == [1.0, 2.0]


@respx.mock
async def test_nothing_is_sent_once_the_budget_is_used_up(session_factory):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    client, budget, _, _ = _client(session_factory, budget=1)
    await client.request("gd_alice", "gmail.profile", "GET", URL)
    with pytest.raises(BudgetReached):
        await client.request("gd_alice", "gmail.profile", "GET", URL)
    assert route.call_count == 1


@respx.mock
async def test_an_unreachable_google_gives_none(session_factory):
    respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
    client, _, _, _ = _client(session_factory)
    assert await client.request("gd_alice", "gmail.profile", "GET", URL) is None


@respx.mock
async def test_a_call_that_may_not_wait_is_not_paced_or_retried(session_factory):
    route = respx.get(URL).mock(return_value=httpx.Response(429, json={}))
    client, budget, pacer, sleeps = _client(session_factory)
    resp = await client.request(
        "gd_alice", "gmail.profile", "GET", URL, enforce_budget=False, wait_out_rate_limits=False
    )
    assert resp.status_code == 429
    assert route.call_count == 1 and pacer.waits == [] and sleeps == []
    assert budget.used_today() == 1


async def test_drive_calls_are_counted_but_never_refused(app):
    api = app.state.drive_api
    api.tokens._cache["gd_alice"] = ("ya29.test", 1e12)
    budget = app.state.budget
    budget.spend(budget.budget())  # the day's budget is used up
    with respx.mock:
        respx.get(f"{FILES_URL}/F1").mock(
            return_value=httpx.Response(200, json={"id": "F1", "trashed": True})
        )
        state = await api.file_state("gd_alice", "F1")
    assert state.trashed is True
    assert budget.used_today() == budget.budget() + 1


def test_a_finished_rclone_run_adds_its_estimate(sync_engine, db, make_account, make_pair):
    got: list[int] = []
    sync_engine.on_api_estimate = got.append
    pair = make_pair(make_account(), initialized=True)
    job = sync_engine.start_job(db, pair, JobKind.BISYNC, Trigger.MANUAL)
    job.files_changed = 3
    sync_engine.finish_job(db, job, JobState.SUCCESS)
    dry = sync_engine.start_job(db, pair, JobKind.DRY_RUN, Trigger.MANUAL)
    sync_engine.finish_job(db, dry, JobState.SUCCESS)  # dry runs list too
    assert got == [130, 100]


def test_usage_api_shows_today_and_the_budget(auth_client, app):
    app.state.budget.spend(42)
    app.state.budget.add_drive_estimate(130)
    body = auth_client.get("/api/google/usage").json()
    assert body["used"] == 42 and body["drive_estimate"] == 130
    assert body["budget"] == 10_000_000 and body["reached"] is False
    assert len(body["day"]) == 10


@respx.mock
async def test_drive_keeps_its_own_http_factory_with_the_google_client(session_factory):
    from app.services.drive_api.client import DriveApi

    used: list[int] = []

    def factory():
        used.append(1)
        return httpx.AsyncClient(timeout=30)

    class Tokens:
        async def access_token(self, name):
            return "ya29.test"

        def forget(self, name):
            pass

    google, _, _, _ = _client(session_factory)
    api = DriveApi(Tokens(), http_factory=factory, google=google)
    respx.get(f"{FILES_URL}/F1").mock(
        return_value=httpx.Response(200, json={"id": "F1", "trashed": False})
    )
    await api.file_state("gd_alice", "F1")
    assert used == [1]


@respx.mock
async def test_a_call_that_is_only_counted_goes_ahead_when_counting_fails(session_factory):
    class BrokenBudget:
        def spend(self, units, *, enforce=True):
            raise RuntimeError("database is locked")

    route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    client = GoogleClient(budget=BrokenBudget(), pacer=None)
    resp = await client.request("gd_alice", "drive.files.get", "GET", URL, enforce_budget=False)
    assert resp.status_code == 200 and route.call_count == 1


@respx.mock
async def test_drive_refreshes_an_expired_token_through_the_google_client(session_factory):
    from app.services.drive_api.client import DriveApi

    forgotten: list[str] = []

    class Tokens:
        async def access_token(self, name):
            return "ya29.test"

        def forget(self, name):
            forgotten.append(name)

    google, budget, _, _ = _client(session_factory)
    api = DriveApi(Tokens(), google=google)
    route = respx.get(f"{FILES_URL}/F1").mock(
        side_effect=[
            httpx.Response(401, json={}),
            httpx.Response(200, json={"id": "F1", "trashed": True}),
        ]
    )
    state = await api.file_state("gd_alice", "F1")
    assert state.trashed is True and route.call_count == 2
    assert forgotten == ["gd_alice"] and budget.used_today() == 2
