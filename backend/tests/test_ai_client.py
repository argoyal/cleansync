import json
import logging

import httpx
import pytest
import respx

from app.services.ai.client import AiClient, AiRefused, AiUnreachable, read_json
from app.services.ai.connections import AiTarget

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
