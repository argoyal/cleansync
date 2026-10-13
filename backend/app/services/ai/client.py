"""One small client for every OpenAI-style server: Ollama, LM Studio, llama.cpp, vLLM, a
LiteLLM gateway, OpenRouter, Groq... (AI rules spec §4). No SDK. Asks for a structured answer
three ways, in order, and remembers which one worked for each connection and model."""

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from app.core.clock import utcnow
from app.services.ai.connections import AiTarget
from app.services.ai.limits import (
    DOWN,
    LIMITED,
    NEEDS_YOU,
    Pause,
    clean_detail,
    kind_of,
    limited_reason,
    pause_end,
)

MODES = ("json_schema", "json_object", "text")
RAW_KEEP = 2000
SHAPE_REFUSED = (400, 404, 405, 415, 422)  # a response_format the server does not take
READ_JSON_CHARS = 8000
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class AiError(Exception):
    pass


class AiUnreachable(AiError):
    pass


class AiRefused(AiError):
    pass


class AiPaused(AiError):
    """The connection is paused (model limits spec §7): no call was made, or the call just
    made paused it. The pause is saved in the database."""

    def __init__(self, pause: Pause) -> None:
        self.pause = pause
        if pause.until is not None:
            text = (
                f"{pause.reason} CleanSync will try again after {pause.until:%Y-%m-%d %H:%M} UTC."
            )
        else:
            text = f"{pause.reason} Check Settings → Models."
        if pause.detail:
            text += f" The server said: {pause.detail}"
        super().__init__(text)


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


class LimitStore(Protocol):
    def pause_of(self, connection_id: int, now=None) -> Pause | None: ...

    def limits_of(self, connection_id: int) -> tuple[str, str, int]: ...

    def set_pause(
        self, connection_id: int, kind: str, until, reason: str, detail, now
    ) -> Pause: ...

    def clear_pause(self, connection_id: int) -> bool: ...

    def secrets_of(self, connection_id: int) -> list[str]: ...


def read_json(text: str) -> dict | None:
    """The JSON object in a model's text: thinking blocks and code fences are dropped, then
    the first {...} that parses wins. None when there is none."""
    # Only the start of the answer is looked at: scanning a long unbalanced text is slow.
    text = _THINK.sub(" ", text or "")[:READ_JSON_CHARS]
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
    """The server did not take this response_format: try the next mode."""


class _Failed(Exception):
    """One call failed: what kind, CleanSync's sentence, and the reply to read limits from."""

    def __init__(self, kind: str, message: str, headers=None, body: str = "") -> None:
        super().__init__(message)
        self.kind, self.message = kind, message
        self.headers, self.body = dict(headers or {}), body


def _origin(url: str) -> str:
    """scheme://host[:port] only, so no user:password or query string reaches a message."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1]}"


class AiClient:
    def __init__(
        self,
        modes: ModeStore,
        *,
        http_factory: Callable[[], httpx.AsyncClient] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        retry_pause_s: float = 2.0,
        limits: LimitStore | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.modes = modes
        self.http_factory = http_factory or (lambda: httpx.AsyncClient())
        self.monotonic = monotonic
        self.retry_pause_s = retry_pause_s
        self.limits = limits
        self.clock = clock

    @staticmethod
    def _headers(t: AiTarget) -> dict[str, str]:
        h = {"Content-Type": "application/json", **t.headers}
        if t.api_key:
            h["Authorization"] = f"Bearer {t.api_key}"
        return h

    async def _post(self, t: AiTarget, body: dict) -> dict:
        """One request with one retry when the server fails or can't be reached. A limit
        reply is never retried. A server that rejects or fails on a response_format raises
        _ShapeRefused so the next mode is tried. A server that can't be reached at all does
        not cycle modes. Every other failure raises _Failed."""
        shaped = "response_format" in body
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
            except httpx.InvalidURL:
                raise _Failed(NEEDS_YOU, "The model's server address isn't valid.") from None
            except httpx.TimeoutException:
                last = f"the model didn't answer within {int(t.timeout_s)} seconds"
            except httpx.HTTPError as e:
                last = f"couldn't reach {_origin(t.base_url)} ({type(e).__name__})"
            else:
                code = resp.status_code
                text = resp.text[:5000] if code >= 400 else ""
                if code >= 400 and kind_of(code, text) == LIMITED:
                    raise _Failed(LIMITED, "", resp.headers, text)  # never retried
                if shaped and code in SHAPE_REFUSED:
                    raise _ShapeRefused()
                if code in (401, 403):
                    raise _Failed(NEEDS_YOU, "The model's server refused the API key.", body=text)
                if code == 404:
                    raise _Failed(
                        NEEDS_YOU,
                        f"The server has no model called {t.model!r}, or the address is wrong.",
                        body=text,
                    )
                if code == 405:
                    raise _Failed(
                        NEEDS_YOU,
                        "The server doesn't accept chat requests at this address. "
                        "It usually ends in /v1.",
                        body=text,
                    )
                if code >= 500 and shaped and attempt == 2:
                    raise _ShapeRefused()
                if code >= 500 or code == 408:
                    last = f"the model's server is failing (HTTP {code})"
                elif code >= 400:
                    raise _Failed(
                        NEEDS_YOU,
                        f"The model's server refused the request (HTTP {code}).",
                        body=text,
                    )
                else:
                    try:
                        return resp.json()
                    except ValueError:
                        last = "the model's server sent something that isn't JSON"
            if attempt == 1:
                await asyncio.sleep(self.retry_pause_s)
        raise _Failed(DOWN, last[0].upper() + last[1:] + ".")

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
        kw = dict(
            system=system, user=user, schema=schema, schema_name=schema_name, parse_text=parse_text
        )
        if self.limits is None:  # no pause store: the old way (plain errors)
            try:
                return await self._ask(target, **kw)
            except _Failed as f:
                raise (AiUnreachable if f.kind == DOWN else AiRefused)(
                    f.message or "The model's server refused the request (limit reached)."
                ) from None
        cid = target.connection_id
        if not target.skip_pause:
            paused = self.limits.pause_of(cid, now=self.clock())
            if paused is not None:
                raise AiPaused(paused)
        try:
            answer = await self._ask(target, **kw)
        except _Failed as f:
            # A Test with typed-in values must not stop live sorting for a typo'd model or a
            # server blip. Only a used-up limit is saved: that is per key, not per model.
            save = not target.skip_pause or f.kind == LIMITED
            raise AiPaused(self._pause(cid, f, save=save)) from None
        self.limits.clear_pause(cid)
        return answer

    def _pause(self, cid: int, f: _Failed, *, save: bool = True) -> Pause:
        now = self.clock()
        resets_at, resets_tz, streak = self.limits.limits_of(cid)
        until = pause_end(f.kind, now, f.headers, resets_at, resets_tz, streak)
        reason = limited_reason(now, until) if f.kind == LIMITED else f.message
        detail = clean_detail(f.body, self.limits.secrets_of(cid)) if f.body else None
        if not save:
            return Pause(f.kind, until, reason, detail, now)
        return self.limits.set_pause(cid, f.kind, until, reason, detail, now)

    async def _ask(
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
        last_answer: AiAnswer | None = None
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
            answer = AiAnswer(
                data=parsed,
                raw=text[:RAW_KEEP],
                mode=mode,
                took_ms=int((self.monotonic() - started) * 1000),
                model=target.model,
            )
            if parsed is not None:
                self.modes.remember_mode(target.connection_id, target.model, mode)
                return answer
            last_answer = answer
        if last_answer is not None:
            return last_answer
        raise _Failed(NEEDS_YOU, "The model's server refused every way of asking.")

    async def list_models(self, target: AiTarget) -> list[str]:
        try:
            async with self.http_factory() as http:
                resp = await http.get(
                    f"{target.base_url}/models",
                    headers=self._headers(target),
                    timeout=target.timeout_s,
                )
        except httpx.InvalidURL:
            raise AiRefused("The model's server address isn't valid.") from None
        except httpx.HTTPError as e:
            raise AiUnreachable(
                f"Couldn't reach {_origin(target.base_url)} ({type(e).__name__})."
            ) from None
        if resp.status_code in (401, 403):
            raise AiRefused("The model's server refused the API key.")
        if resp.status_code >= 400:
            raise AiRefused(f"The server didn't list its models (HTTP {resp.status_code}).")
        try:
            items = resp.json().get("data") or []
        except (ValueError, AttributeError):
            return []
        return sorted({str(i["id"]) for i in items if isinstance(i, dict) and i.get("id")})
