"""One small client for every OpenAI-style server: Ollama, LM Studio, llama.cpp, vLLM, a
LiteLLM gateway, OpenRouter, Groq... (AI rules spec §4). No SDK. Asks for a structured answer
three ways, in order, and remembers which one worked for each connection and model."""

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.services.ai.connections import AiTarget

log = logging.getLogger(__name__)
MODES = ("json_schema", "json_object", "text")
RAW_KEEP = 2000
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class AiError(Exception):
    pass


class AiUnreachable(AiError):
    pass


class AiRefused(AiError):
    pass


@dataclass
class AiAnswer:
    data: dict | None
    raw: str
    mode: str
    took_ms: int
    model: str


class ModeStore(Protocol):
    def remembered_mode(self, connection_id: int, model: str) -> str | None: ...

    def remember_mode(self, connection_id: int, model: str, mode: str) -> None: ...


def read_json(text: str) -> dict | None:
    """The JSON object in a model's text: thinking blocks and code fences are dropped, then
    the first {...} that parses wins. None when there is none."""
    text = _THINK.sub(" ", text or "")
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for cand in candidates:
        start = cand.find("{")
        while start >= 0:
            depth = 0
            for i in range(start, len(cand)):
                if cand[i] == "{":
                    depth += 1
                elif cand[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(cand[start : i + 1])
                        except ValueError:
                            break
                        if isinstance(data, dict):
                            return data
                        break
            start = cand.find("{", start + 1)
    return None


class _ShapeRefused(Exception):
    """HTTP 400 for a response_format: try the next mode."""


class AiClient:
    def __init__(
        self,
        modes: ModeStore,
        *,
        http_factory: Callable[[], httpx.AsyncClient] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        retry_pause_s: float = 2.0,
    ) -> None:
        self.modes = modes
        self.http_factory = http_factory or (lambda: httpx.AsyncClient())
        self.monotonic = monotonic
        self.retry_pause_s = retry_pause_s

    @staticmethod
    def _headers(t: AiTarget) -> dict[str, str]:
        h = {"Content-Type": "application/json", **t.headers}
        if t.api_key:
            h["Authorization"] = f"Bearer {t.api_key}"
        return h

    async def _post(self, t: AiTarget, body: dict) -> dict:
        """One request with one retry when the server can't be reached."""
        last = "the model's server didn't answer"
        for attempt in (1, 2):
            try:
                async with self.http_factory() as http:
                    resp = await http.post(
                        f"{t.base_url}/chat/completions",
                        json=body,
                        headers=self._headers(t),
                        timeout=t.timeout_s,
                    )
            except httpx.TimeoutException:
                last = f"the model didn't answer within {int(t.timeout_s)} seconds"
            except httpx.HTTPError as e:
                last = f"couldn't reach {t.base_url} ({type(e).__name__})"
            else:
                if resp.status_code == 400 and "response_format" in body:
                    raise _ShapeRefused()
                if resp.status_code in (401, 403):
                    raise AiRefused("The model's server refused the API key.")
                if resp.status_code == 404:
                    raise AiRefused(
                        f"The server has no model called {t.model!r}, or the address is wrong."
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = f"the model's server is busy or failing (HTTP {resp.status_code})"
                elif resp.status_code >= 400:
                    raise AiRefused(
                        f"The model's server refused the request (HTTP {resp.status_code})."
                    )
                else:
                    try:
                        return resp.json()
                    except ValueError:
                        last = "the model's server sent something that isn't JSON"
            if attempt == 1:
                await asyncio.sleep(self.retry_pause_s)
        raise AiUnreachable(last[0].upper() + last[1:] + ".")

    @staticmethod
    def _content(data: dict) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""
        return content if isinstance(content, str) else ""

    async def ask(
        self,
        target: AiTarget,
        *,
        system: str,
        user: str,
        schema: dict,
        schema_name: str,
        parse_text: Callable[[str], dict | None],
    ) -> AiAnswer:
        first = self.modes.remembered_mode(target.connection_id, target.model)
        order = [first] + [m for m in MODES if m != first] if first in MODES else list(MODES)
        started = self.monotonic()
        for mode in order:
            body: dict = {
                "model": target.model,
                "temperature": target.temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            if mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                }
            elif mode == "json_object":
                body["response_format"] = {"type": "json_object"}
            try:
                data = await self._post(target, body)
            except _ShapeRefused:
                continue
            text = self._content(data)
            parsed = read_json(text) if mode != "text" else None
            if parsed is None:
                parsed = parse_text(_THINK.sub(" ", text)) or read_json(text)
            self.modes.remember_mode(target.connection_id, target.model, mode)
            return AiAnswer(
                data=parsed,
                raw=text[:RAW_KEEP],
                mode=mode,
                took_ms=int((self.monotonic() - started) * 1000),
                model=target.model,
            )
        raise AiRefused("The model's server refused every way of asking.")

    async def list_models(self, target: AiTarget) -> list[str]:
        try:
            async with self.http_factory() as http:
                resp = await http.get(
                    f"{target.base_url}/models",
                    headers=self._headers(target),
                    timeout=target.timeout_s,
                )
        except httpx.HTTPError as e:
            raise AiUnreachable(f"Couldn't reach {target.base_url} ({type(e).__name__}).") from None
        if resp.status_code in (401, 403):
            raise AiRefused("The model's server refused the API key.")
        if resp.status_code >= 400:
            raise AiRefused(f"The server didn't list its models (HTTP {resp.status_code}).")
        try:
            items = resp.json().get("data") or []
        except (ValueError, AttributeError):
            return []
        return sorted({str(i["id"]) for i in items if isinstance(i, dict) and i.get("id")})
