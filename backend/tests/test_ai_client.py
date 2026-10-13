import dataclasses
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.services.ai.client import AiClient, AiPaused, AiRefused, AiUnreachable, read_json
from app.services.ai.connections import AiTarget
from app.services.ai.limits import DOWN, LIMITED, NEEDS_YOU, Pause

URL = "http://ollama.example.com:11434/v1"
SCHEMA = {"type": "object", "properties": {"rule": {"type": "integer"}}, "required": ["rule"]}


class Modes:
    def __init__(self):
        self.saved: dict = {}

    def remembered_mode(self, cid, model):
        return self.saved.get((cid, model))

    def remember_mode(self, cid, model, mode):
        self.saved[(cid, model)] = mode


def target(key=None):
    return AiTarget(1, URL, key, {}, "llama3.1:8b", 0.0, 5.0)


def reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def parse_text(text):
    import re

    m = re.search(r"Rule:\s*(\d+)", text)
    return {"rule": int(m.group(1))} if m else None


async def ask(client, t=None):
    return await client.ask(
        t or target(),
        system="s",
        user="u",
        schema=SCHEMA,
        schema_name="choice",
        parse_text=parse_text,
    )


@respx.mock
async def test_json_schema_answer_is_read_and_mode_remembered():
    modes = Modes()
    route = respx.post(f"{URL}/chat/completions").mock(return_value=reply('{"rule": 2}'))
    ans = await ask(AiClient(modes, retry_pause_s=0))
    assert ans.data == {"rule": 2} and ans.mode == "json_schema"
    body = json.loads(route.calls[0].request.content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["temperature"] == 0.0 and body["model"] == "llama3.1:8b"
    assert modes.saved[(1, "llama3.1:8b")] == "json_schema"


@respx.mock
async def test_falls_back_when_json_schema_is_refused():
    modes = Modes()

    def handler(request):
        fmt = json.loads(request.content).get("response_format")
        if fmt and fmt["type"] == "json_schema":
            return httpx.Response(400, json={"error": "response_format not supported"})
        if fmt and fmt["type"] == "json_object":
            return httpx.Response(400, json={"error": "nope"})
        return reply("I think this is a newsletter.\nRule: 3")

    respx.post(f"{URL}/chat/completions").mock(side_effect=handler)
    ans = await ask(AiClient(modes, retry_pause_s=0))
    assert ans.data == {"rule": 3} and ans.mode == "text"
    assert modes.saved[(1, "llama3.1:8b")] == "text"


@respx.mock
async def test_remembered_mode_is_used_first():
    modes = Modes()
    modes.saved[(1, "llama3.1:8b")] = "json_object"
    route = respx.post(f"{URL}/chat/completions").mock(return_value=reply('{"rule": 1}'))
    await ask(AiClient(modes, retry_pause_s=0))
    assert json.loads(route.calls[0].request.content)["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    "content,expected",
    [
        ('```json\n{"rule": 4, "why": "x"}\n```', {"rule": 4, "why": "x"}),
        ('<think>hmm {"rule": 9}</think>\n{"rule": 1}', {"rule": 1}),
        ('Sure! Here it is: {"rule": 5} hope that helps', {"rule": 5}),
        ("no json here", None),
        ("", None),
    ],
)
def test_read_json_finds_the_object(content, expected):
    assert read_json(content) == expected


@respx.mock
async def test_unreadable_answer_is_none_not_an_error():
    respx.post(f"{URL}/chat/completions").mock(return_value=reply("I cannot decide."))
    ans = await ask(AiClient(Modes(), retry_pause_s=0))
    assert ans.data is None and ans.raw == "I cannot decide."


@respx.mock
async def test_one_retry_then_unreachable():
    route = respx.post(f"{URL}/chat/completions").mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(AiUnreachable):
        await ask(AiClient(Modes(), retry_pause_s=0))
    assert route.call_count == 2


@respx.mock
async def test_server_error_then_success():
    respx.post(f"{URL}/chat/completions").mock(
        side_effect=[httpx.Response(503), reply('{"rule": 0}')]
    )
    ans = await ask(AiClient(Modes(), retry_pause_s=0))
    assert ans.data == {"rule": 0}


@respx.mock
async def test_bad_key_is_refused_with_plain_reason():
    respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(401))
    with pytest.raises(AiRefused, match="refused the API key"):
        await ask(AiClient(Modes(), retry_pause_s=0), target("sk-wrong-key-1"))


@respx.mock
async def test_key_and_headers_are_sent_but_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    t = AiTarget(1, URL, "sk-secret-999999", {"X-Team": "hidden-header-1"}, "m", 0.0, 5.0)
    route = respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(500))
    with pytest.raises(AiUnreachable) as e:
        await ask(AiClient(Modes(), retry_pause_s=0), t)
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer sk-secret-999999"
    assert req.headers["X-Team"] == "hidden-header-1"
    for text in (caplog.text, str(e.value)):
        assert "sk-secret-999999" not in text and "hidden-header-1" not in text


@respx.mock
async def test_list_models():
    respx.get(f"{URL}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]})
    )
    assert await AiClient(Modes()).list_models(target()) == ["a", "b"]


def _mode_of(request) -> str | None:
    fmt = json.loads(request.content).get("response_format")
    return fmt["type"] if fmt else None


@pytest.mark.parametrize(
    "status,sent",
    [
        (422, ["json_schema", "json_object"]),
        (404, ["json_schema", "json_object"]),
        (500, ["json_schema", "json_schema", "json_object"]),
    ],
)
@respx.mock
async def test_unsupported_json_schema_falls_through_to_json_object(status, sent):
    modes = Modes()

    def handler(request):
        mode = _mode_of(request)
        if mode == "json_schema":
            return httpx.Response(status, json={"error": "unsupported"})
        return reply('{"rule": 6}')

    route = respx.post(f"{URL}/chat/completions").mock(side_effect=handler)
    ans = await ask(AiClient(modes, retry_pause_s=0))
    assert ans.data == {"rule": 6} and ans.mode == "json_object"
    assert [_mode_of(c.request) for c in route.calls] == sent
    assert modes.saved[(1, "llama3.1:8b")] == "json_object"


@respx.mock
async def test_unreadable_json_schema_then_readable_json_object():
    modes = Modes()

    def handler(request):
        if _mode_of(request) == "json_schema":
            return reply("I cannot decide.")
        return reply('{"rule": 7}')

    route = respx.post(f"{URL}/chat/completions").mock(side_effect=handler)
    ans = await ask(AiClient(modes, retry_pause_s=0))
    assert ans.data == {"rule": 7} and ans.mode == "json_object"
    assert [_mode_of(c.request) for c in route.calls] == ["json_schema", "json_object"]
    assert modes.saved[(1, "llama3.1:8b")] == "json_object"


@respx.mock
async def test_unreadable_everywhere_is_none_and_nothing_remembered():
    modes = Modes()
    route = respx.post(f"{URL}/chat/completions").mock(return_value=reply("I cannot decide."))
    ans = await ask(AiClient(modes, retry_pause_s=0))
    assert ans.data is None and ans.mode == "text"
    assert route.call_count == 3
    assert modes.saved == {}


@respx.mock
async def test_connect_error_does_not_cycle_modes():
    route = respx.post(f"{URL}/chat/completions").mock(side_effect=httpx.ConnectError("c"))
    with pytest.raises(AiUnreachable):
        await ask(AiClient(Modes(), retry_pause_s=0))
    assert route.call_count == 2


@respx.mock
async def test_userinfo_never_appears_in_error_text():
    secret_url = "http://user:pass-secret-1@ollama.example.com:11434/v1"
    respx.route(host="ollama.example.com").mock(side_effect=httpx.ConnectError("c"))
    t = AiTarget(1, secret_url, None, {}, "m", 0.0, 5.0)
    with pytest.raises(AiUnreachable) as e:
        await ask(AiClient(Modes(), retry_pause_s=0), t)
    assert "pass-secret-1" not in str(e.value) and "user:" not in str(e.value)
    assert "http://ollama.example.com:11434" in str(e.value)


@respx.mock
async def test_text_mode_404_is_still_refused_about_the_model():
    respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(404))
    with pytest.raises(AiRefused, match="no model called"):
        await ask(AiClient(Modes(), retry_pause_s=0))


async def test_invalid_address_is_refused_plainly():
    t = AiTarget(1, "http://example.com:notaport/v1", None, {}, "m", 0.0, 5.0)
    with pytest.raises(AiRefused, match="address isn't valid"):
        await ask(AiClient(Modes(), retry_pause_s=0), t)


def test_read_json_gives_up_quickly_on_a_long_unbalanced_answer():
    import time

    text = "{" * 20_000 + '{"rule": 1}'
    started = time.monotonic()
    assert read_json(text) is None  # the object sits past the first 8,000 characters
    assert time.monotonic() - started < 2
    assert read_json('{"rule": 1}' + " " * 100_000) == {"rule": 1}


NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


class Limits:
    """An in-memory stand-in for AiSettings' pause methods."""

    def __init__(self, resets=("00:00", "UTC"), streak=0, pause=None, secrets=()):
        self.resets, self.streak, self.pause, self._secrets = resets, streak, pause, list(secrets)
        self.cleared = 0

    def pause_of(self, cid, now=None):
        p = self.pause
        if p is None or (p.until is not None and p.until <= (now or NOW)):
            return None
        return p

    def limits_of(self, cid):
        return (*self.resets, self.streak)

    def set_pause(self, cid, kind, until, reason, detail, now):
        if kind == DOWN:
            self.streak += 1
        self.pause = Pause(kind, until, reason, detail, now)
        return self.pause

    def clear_pause(self, cid):
        had = self.pause is not None or self.streak > 0
        self.pause, self.streak = None, 0
        self.cleared += had
        return had

    def secrets_of(self, cid):
        return self._secrets


def limited_client(limits):
    return AiClient(Modes(), limits=limits, retry_pause_s=0, clock=lambda: NOW)


@respx.mock
async def test_429_with_retry_after_pauses_and_is_not_retried():
    limits = Limits()
    route = respx.post(f"{URL}/chat/completions").mock(
        return_value=httpx.Response(
            429,
            headers={"Retry-After": "3600"},
            json={"error": {"message": "Rate limit reached for requests per day"}},
        )
    )
    with pytest.raises(AiPaused) as e:
        await ask(limited_client(limits))
    assert route.call_count == 1  # no retry, no other answer format
    p = e.value.pause
    assert p.kind == LIMITED and p.until == NOW + timedelta(hours=1)
    assert p.detail == "Rate limit reached for requests per day"
    assert limits.pause is p


@respx.mock
async def test_429_without_headers_pauses_until_the_daily_reset():
    limits = Limits(resets=("05:30", "UTC"))
    respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(429))
    with pytest.raises(AiPaused) as e:
        await ask(limited_client(limits))
    assert e.value.pause.until == datetime(2026, 10, 14, 5, 30, tzinfo=UTC)


@respx.mock
async def test_quota_400_does_not_cycle_answer_formats():
    route = respx.post(f"{URL}/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "Quota exceeded"}})
    )
    with pytest.raises(AiPaused) as e:
        await ask(limited_client(Limits()))
    assert route.call_count == 1 and e.value.pause.kind == LIMITED


@respx.mock
async def test_402_is_limited():
    respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(402))
    with pytest.raises(AiPaused) as e:
        await ask(limited_client(Limits()))
    assert e.value.pause.kind == LIMITED


@pytest.mark.parametrize(
    "status,text,kind",
    [
        (403, "rate limit for this key", LIMITED),
        (403, "forbidden", NEEDS_YOU),
        (401, "", NEEDS_YOU),
        (405, "", NEEDS_YOU),
    ],
)
@respx.mock
async def test_plain_text_replies_are_sorted(status, text, kind):
    # A remembered "text" mode sends no response_format, so the status is judged directly.
    m = Modes()
    m.saved[(1, "llama3.1:8b")] = "text"
    respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(status, text=text))
    with pytest.raises(AiPaused) as e:
        await ask(AiClient(m, limits=Limits(), retry_pause_s=0, clock=lambda: NOW))
    assert e.value.pause.kind == kind
    if status == 405:
        assert "/v1" in e.value.pause.reason


@respx.mock
async def test_503_twice_is_down_on_the_first_step():
    limits = Limits()
    m = Modes()
    m.saved[(1, "llama3.1:8b")] = "text"
    route = respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(503))
    with pytest.raises(AiPaused) as e:
        await ask(AiClient(m, limits=limits, retry_pause_s=0, clock=lambda: NOW))
    assert route.call_count == 2 and e.value.pause.kind == DOWN
    assert e.value.pause.until == NOW + timedelta(minutes=1) and limits.streak == 1


@respx.mock
async def test_timeout_is_down():
    m = Modes()
    m.saved[(1, "llama3.1:8b")] = "text"
    respx.post(f"{URL}/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AiPaused) as e:
        await ask(AiClient(m, limits=Limits(), retry_pause_s=0, clock=lambda: NOW))
    assert e.value.pause.kind == DOWN


@respx.mock
async def test_paused_connection_makes_no_call():
    route = respx.post(f"{URL}/chat/completions").mock(return_value=reply('{"rule": 1}'))
    limits = Limits(pause=Pause(LIMITED, NOW + timedelta(hours=2), "r", None, NOW))
    with pytest.raises(AiPaused):
        await ask(limited_client(limits))
    assert route.call_count == 0


@respx.mock
async def test_ended_pause_lets_the_call_through_and_success_clears_it():
    respx.post(f"{URL}/chat/completions").mock(return_value=reply('{"rule": 1}'))
    limits = Limits(pause=Pause(LIMITED, NOW - timedelta(seconds=1), "r", None, NOW), streak=2)
    ans = await ask(limited_client(limits))
    assert ans.data == {"rule": 1} and limits.pause is None and limits.streak == 0


@respx.mock
async def test_skip_pause_calls_anyway():
    respx.post(f"{URL}/chat/completions").mock(return_value=reply('{"rule": 1}'))
    limits = Limits(pause=Pause(NEEDS_YOU, None, "r", None, NOW))
    ans = await ask(limited_client(limits), dataclasses.replace(target(), skip_pause=True))
    assert ans.data == {"rule": 1} and limits.pause is None


@respx.mock
async def test_detail_never_carries_the_key():
    respx.post(f"{URL}/chat/completions").mock(
        return_value=httpx.Response(
            429, json={"error": {"message": "limit for key sk-live-abcdef123456"}}
        )
    )
    limits = Limits(secrets=["sk-live-abcdef123456"])
    with pytest.raises(AiPaused) as e:
        await ask(limited_client(limits), target("sk-live-abcdef123456"))
    assert "sk-live-abcdef123456" not in str(e.value) and "sk-live" not in (
        e.value.pause.detail or ""
    )


def test_paused_message_shapes():
    lim = AiPaused(Pause(LIMITED, NOW, "The model's usage limit is used up.", None, NOW))
    assert (
        str(lim)
        == "The model's usage limit is used up. CleanSync will try again after 2026-10-13 10:00 UTC."
    )
    you = AiPaused(
        Pause(NEEDS_YOU, None, "The model's server refused the API key.", "bad key", NOW)
    )
    assert (
        str(you)
        == "The model's server refused the API key. Check Settings → Models. The server said: bad key"
    )


@respx.mock
async def test_a_failed_test_call_saves_no_needs_you_or_down_pause():
    # Test (skip_pause) must not stop live sorting for a typo'd model.
    limits = Limits()
    respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(404, text="nope"))
    t = dataclasses.replace(target(), skip_pause=True)
    with pytest.raises(AiPaused) as e:
        await ask(limited_client(limits), t)
    assert e.value.pause.kind == NEEDS_YOU and "no model called" in e.value.pause.reason
    assert limits.pause is None
    respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(503))
    with pytest.raises(AiPaused) as e:
        await ask(limited_client(limits), t)
    assert e.value.pause.kind == DOWN and limits.pause is None and limits.streak == 0


@respx.mock
async def test_a_failed_test_call_still_saves_a_limit_pause():
    limits = Limits()
    respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(429))
    t = dataclasses.replace(target(), skip_pause=True)
    with pytest.raises(AiPaused) as e:
        await ask(limited_client(limits), t)
    assert e.value.pause.kind == LIMITED and limits.pause is e.value.pause


@respx.mock
async def test_408_is_retried_then_down():
    limits = Limits()
    m = Modes()
    m.saved[(1, "llama3.1:8b")] = "text"
    route = respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(408))
    with pytest.raises(AiPaused) as e:
        await ask(AiClient(m, limits=limits, retry_pause_s=0, clock=lambda: NOW))
    assert route.call_count == 2 and e.value.pause.kind == DOWN
