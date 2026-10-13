# Model Limits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a model provider says its limit is used up, CleanSync stops calling that connection until the limit resets. All waiting mail stays queued and is not charged a try, and the pause is saved in the database so it survives a restart.

**Architecture:**
- A new pure module, `app/services/ai/limits.py`, sorts a failed reply into limited, down or needs_you, and works out when the pause ends.
- `AiSettings` saves the pause in new columns on `ai_connections` (migration 0010).
- `AiClient` checks the saved pause before every call (the gate) and writes a new pause after a failure.
- The sorting worker, drafting, the API and the UI react to one new error type, `AiPaused`.

**Tech Stack:** FastAPI, SQLAlchemy 2 + Alembic on SQLite, httpx + respx, pytest (`asyncio_mode = "auto"`), React 18 + TanStack Query + Tailwind, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-13-model-limits-design.md` (approved)

## Global Constraints

- **Storage:** every pause and its reason are written to the database before CleanSync acts on them. No pause state lives only in memory.
- **What pauses:** the whole connection. Every task and mailbox using that connection pauses together.
- **Limit words** (case is ignored): `rate limit`, `rate_limit`, `ratelimit`, `quota`, `limit exceeded`, `limit reached`, `too many requests`, `credits`, `exhausted`.
- **Down ladder:** 1, 5, 15, 60, then 360 minutes for every later step.
- **Provider waits:** a wait the provider gives is capped at 7 days and is never shorter than 5 seconds.
- **Daily reset:** the default is `00:00` in `UTC`. A reset less than 1 minute away moves to the next day.
- **`pause_detail`:** cut to 500 characters, with key-like text replaced by `[removed]`.
- **No backup models, no request budgets, no token counting.**
- **Copy:** plain everyday words, matching the existing UI text. No jargon.
- **Tests must pass:** backend `cd backend && uv run pytest -q`, lint `uv run ruff check . && uv run ruff format --check .`. Frontend `cd frontend && npm test -- --run && npm run lint && npm run typecheck`.
- **Commits:** every commit message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Plan decisions on the spec

These settle details the spec left open. They are recorded here so the reviewers judge against them.

1. **The worker needs no new sleep logic (spec §8).** A mail stopped by `AiPaused` gets `next_try_at = pause end`, or now + 1 hour for needs_you. So the existing loop finds nothing due and idles every 15 seconds with no model calls. A mail that arrives during a pause reaches the gate, which raises at once with no network call.
2. **The batch keeps going after `AiPaused`** instead of stopping. The next mail may be decided without the model (a remembered sender or a fixed-only rule), and every mail that does need the model is refused by the gate at no cost.
3. **Resume now wakes every waiting to-do row** (`next_try_at > now`, set to now), not only rows held by this connection. Rows aren't tagged with a connection. The only other rows waiting are ones whose mail copy couldn't be read, and they simply get tried a little early.
4. **`list_models` ignores pauses and never writes one.** It is a settings action (spec §7 already exempted needs_you).
5. **Long runs of characters are only removed when 32 or more long and containing both letters and digits,** not 24 as in spec §9. This keeps model names such as `meta-llama-3.1-8b-instruct` readable in the provider's message.
6. **`AiClient(modes, limits=None)`.** Without a limits store, the client behaves as it does today and raises `AiUnreachable`/`AiRefused`, so existing test fakes keep working. `main.py` always passes `limits=app.state.ai`, and a wiring test checks that.
7. **The browser test (spec §12)** checks Test, the paused badge and Resume now through the **Writing rules** task with a `fake-limited` model. It doesn't turn on sorting. The status line text is covered by vitest.

## Review Focus

1. **A pause that has already ended must not block.** After a restart the next call goes through, and a call that works clears the row. Owned by Task 3, with a test.
2. **A 400 or 422 with quota words must not loop through the three answer formats.** Owned by Task 3, with a test.
3. **Retry dates the provider sends in the past or far future.** A `Retry-After` HTTP date in the past gives 5 seconds; one 30 days away gives 7 days. Owned by Task 1, with a test.
4. **The reset time zone across a daylight-saving change** (America/Los_Angeles, 2026-11-01). Owned by Task 1, with a test.
5. **The provider's message carries the key back.** Some providers echo part of the key in the error. Owned by Task 1 (`clean_detail` with the connection's own key) and Task 5 (API never shows it), with tests.

---

### Task 1: The limits module (pure functions)

**Files:**
- Create: `backend/app/services/ai/limits.py`
- Test: `backend/tests/test_ai_limits.py`

**Interfaces:**
- Produces:
  - `LIMITED = "limited"`, `DOWN = "down"`, `NEEDS_YOU = "needs_you"`
  - `@dataclass(frozen=True) class Pause: kind: str; until: datetime | None; reason: str; detail: str | None; since: datetime`
  - `has_limit_words(text: str) -> bool`
  - `kind_of(status: int, text: str) -> str` (only for HTTP replies of 400 or above)
  - `parse_duration(value: str) -> float | None` (seconds)
  - `provider_wait(headers: Mapping[str, str], now: datetime) -> datetime | None`
  - `next_reset(now: datetime, resets_at: str, resets_tz: str) -> datetime` (UTC)
  - `pause_end(kind: str, now: datetime, headers: Mapping[str, str], resets_at: str, resets_tz: str, down_streak: int) -> datetime | None`
  - `limited_reason(now: datetime, until: datetime) -> str`
  - `clean_detail(body: str, secrets: Iterable[str] = ()) -> str | None`
  - `check_reset(resets_at: str, resets_tz: str) -> None` (raises `ValueError` with a plain message)

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_ai_limits.py
from datetime import UTC, datetime, timedelta

import pytest

from app.services.ai.limits import (
    DOWN,
    LIMITED,
    NEEDS_YOU,
    check_reset,
    clean_detail,
    has_limit_words,
    kind_of,
    limited_reason,
    next_reset,
    parse_duration,
    pause_end,
    provider_wait,
)

NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "text",
    ["Rate limit reached for requests", "QUOTA exceeded", "rate_limit_exceeded",
     "Too Many Requests", "You have exhausted your credits", "daily limit reached"],
)
def test_limit_words(text):
    assert has_limit_words(text)


def test_no_limit_words():
    assert not has_limit_words("model not found")


@pytest.mark.parametrize(
    "status,text,kind",
    [
        (429, "", LIMITED),
        (402, "", LIMITED),
        (400, '{"error":{"message":"Quota exceeded for model"}}', LIMITED),
        (403, "rate limit for this key", LIMITED),
        (403, "forbidden", NEEDS_YOU),
        (401, "", NEEDS_YOU),
        (404, "", NEEDS_YOU),
        (422, "bad field", NEEDS_YOU),
        (500, "", DOWN),
        (503, "quota service down", DOWN),
    ],
)
def test_kind_of(status, text, kind):
    assert kind_of(status, text) == kind


@pytest.mark.parametrize(
    "value,seconds",
    [("20", 20.0), ("20s", 20.0), ("6m0s", 360.0), ("1h2m3.5s", 3723.5), ("250ms", 0.25),
     ("", None), ("soon", None)],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value) == seconds


def test_provider_wait_retry_after_seconds_and_date():
    assert provider_wait({"Retry-After": "120"}, NOW) == NOW + timedelta(seconds=120)
    when = provider_wait({"retry-after": "Tue, 13 Oct 2026 12:00:00 GMT"}, NOW)
    assert when == datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def test_provider_wait_reset_headers_latest_wins():
    h = {"x-ratelimit-reset-requests": "6m0s", "x-ratelimit-reset-tokens": "30s"}
    assert provider_wait(h, NOW) == NOW + timedelta(minutes=6)


def test_provider_wait_unix_time():
    ts = int((NOW + timedelta(hours=3)).timestamp())
    assert provider_wait({"x-ratelimit-reset": str(ts)}, NOW) == NOW + timedelta(hours=3)


def test_provider_wait_none():
    assert provider_wait({}, NOW) is None
    assert provider_wait({"Retry-After": "later"}, NOW) is None


def test_next_reset_utc_midnight():
    assert next_reset(NOW, "00:00", "UTC") == datetime(2026, 10, 14, 0, 0, tzinfo=UTC)
    just_before = datetime(2026, 10, 13, 23, 59, 30, tzinfo=UTC)  # less than a minute away
    assert next_reset(just_before, "00:00", "UTC") == datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    just_after = datetime(2026, 10, 14, 0, 0, 30, tzinfo=UTC)
    assert next_reset(just_after, "00:00", "UTC") == datetime(2026, 10, 15, 0, 0, tzinfo=UTC)


def test_next_reset_pacific_across_daylight_saving():
    # 2026-11-01 02:00 PDT -> PST. Midnight Pacific is 07:00 UTC before, 08:00 UTC after.
    before = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert next_reset(before, "00:00", "America/Los_Angeles") == datetime(
        2026, 11, 1, 7, 0, tzinfo=UTC
    )
    after = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    assert next_reset(after, "00:00", "America/Los_Angeles") == datetime(
        2026, 11, 2, 8, 0, tzinfo=UTC
    )


def test_pause_end_limited_uses_provider_then_daily():
    assert pause_end(LIMITED, NOW, {"Retry-After": "20"}, "00:00", "UTC", 0) == NOW + timedelta(
        seconds=20
    )
    assert pause_end(LIMITED, NOW, {}, "00:00", "UTC", 0) == datetime(2026, 10, 14, tzinfo=UTC)


def test_pause_end_limited_clamps():
    far = (NOW + timedelta(days=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert pause_end(LIMITED, NOW, {"Retry-After": far}, "00:00", "UTC", 0) == NOW + timedelta(
        days=7
    )
    past = (NOW - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert pause_end(LIMITED, NOW, {"Retry-After": past}, "00:00", "UTC", 0) == NOW + timedelta(
        seconds=5
    )


@pytest.mark.parametrize("streak,minutes", [(0, 1), (1, 5), (2, 15), (3, 60), (4, 360), (9, 360)])
def test_pause_end_down_ladder(streak, minutes):
    assert pause_end(DOWN, NOW, {}, "00:00", "UTC", streak) == NOW + timedelta(minutes=minutes)


def test_pause_end_needs_you_has_no_end():
    assert pause_end(NEEDS_YOU, NOW, {}, "00:00", "UTC", 0) is None


def test_limited_reason_short_and_long():
    assert "slow down" in limited_reason(NOW, NOW + timedelta(seconds=30))
    assert "limit is used up" in limited_reason(NOW, NOW + timedelta(hours=5))


def test_clean_detail_takes_the_message_and_removes_keys():
    body = '{"error": {"message": "Invalid key sk-abc123def456ghi789 for nvapi-XYZ123abc"}}'
    out = clean_detail(body)
    assert out.startswith("Invalid key") and "sk-abc" not in out and "nvapi-XYZ" not in out
    assert "[removed]" in out


def test_clean_detail_removes_the_connections_own_secret_and_long_tokens():
    out = clean_detail("bad key MYSECRETVALUE99 and Zq81kd93jfk20dk3k4l5m6n7b8v9c0x1z2", ["MYSECRETVALUE99"])
    assert "MYSECRETVALUE99" not in out and "Zq81kd93" not in out


def test_clean_detail_keeps_model_names():
    out = clean_detail('{"message": "no model meta-llama-3.1-8b-instruct"}')
    assert out == "no model meta-llama-3.1-8b-instruct"


def test_clean_detail_cut_and_empty():
    assert len(clean_detail("x " * 600)) <= 500
    assert clean_detail("") is None and clean_detail("   ") is None


def test_check_reset():
    check_reset("05:30", "Europe/Berlin")
    with pytest.raises(ValueError):
        check_reset("25:00", "UTC")
    with pytest.raises(ValueError):
        check_reset("00:00", "Mars/Olympus")
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_ai_limits.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.ai.limits'`.

- [ ] **Step 3: Write the module**

```python
# backend/app/services/ai/limits.py
"""Model limits (model limits spec §4-5): what kind of failure a reply is, and how long the
connection pauses for it. Pure functions; the pause itself is saved by AiSettings."""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.redact import redact

LIMITED, DOWN, NEEDS_YOU = "limited", "down", "needs_you"
LIMIT_WORDS = (
    "rate limit", "rate_limit", "ratelimit", "quota", "limit exceeded", "limit reached",
    "too many requests", "credits", "exhausted",
)
DOWN_STEPS = tuple(timedelta(minutes=m) for m in (1, 5, 15, 60, 360))
MIN_WAIT = timedelta(seconds=5)
MAX_WAIT = timedelta(days=7)
SHORT_WAIT = timedelta(minutes=2)
RESET_SOON = timedelta(minutes=1)
DETAIL_CHARS = 500
REMOVED = "[removed]"
_RESET_HEADERS = ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens", "x-ratelimit-reset")
_DURATION = re.compile(r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_KEYISH = (
    re.compile(r"\b(?:sk|nvapi|gsk|pk|rk)[-_][A-Za-z0-9_-]{6,}"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}\b"),
)


@dataclass(frozen=True)
class Pause:
    kind: str
    until: datetime | None  # None: needs_you, until the owner acts
    reason: str
    detail: str | None
    since: datetime


def has_limit_words(text: str) -> bool:
    low = (text or "").lower()
    return any(w in low for w in LIMIT_WORDS)


def kind_of(status: int, text: str) -> str:
    if status in (429, 402):
        return LIMITED
    if status >= 500:
        return DOWN
    return LIMITED if has_limit_words(text) else NEEDS_YOU


def parse_duration(value: str) -> float | None:
    v = (value or "").strip().lower()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        pass
    m = _DURATION.match(v)
    if not m or not any(m.groups()):
        return None
    h, mins, s, ms = (float(g) if g else 0.0 for g in m.groups())
    return h * 3600 + mins * 60 + s + ms / 1000


def _one_wait(name: str, value: str, now: datetime) -> datetime | None:
    if name == "retry-after":
        secs = parse_duration(value)
        if secs is not None:
            return now + timedelta(seconds=secs)
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        return when if when.tzinfo else when.replace(tzinfo=UTC)
    secs = parse_duration(value)
    if secs is None:
        return None
    if secs > 1_000_000_000:  # a Unix time, not a number of seconds
        return datetime.fromtimestamp(secs, tz=UTC)
    return now + timedelta(seconds=secs)


def provider_wait(headers: Mapping[str, str], now: datetime) -> datetime | None:
    lower = {str(k).lower(): str(v) for k, v in headers.items()}
    waits = [
        w
        for name in ("retry-after", *_RESET_HEADERS)
        if name in lower and (w := _one_wait(name, lower[name], now)) is not None
    ]
    return max(waits) if waits else None


def check_reset(resets_at: str, resets_tz: str) -> None:
    if not _HHMM.match(resets_at or ""):
        raise ValueError("Use a time like 00:00 or 08:30 for when the daily limit resets.")
    try:
        ZoneInfo(resets_tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"{resets_tz!r} isn't a time zone CleanSync knows, such as UTC or America/Los_Angeles.") from None


def next_reset(now: datetime, resets_at: str, resets_tz: str) -> datetime:
    zone = ZoneInfo(resets_tz)
    hh, mm = (int(x) for x in resets_at.split(":"))
    local = now.astimezone(zone)
    day = local.date()
    for _ in range(3):
        cand = datetime.combine(day, time(hh, mm), tzinfo=zone).astimezone(UTC)
        if cand - now >= RESET_SOON:
            return cand
        day += timedelta(days=1)
    return now + timedelta(days=1)  # not reached: three days always hold a reset


def pause_end(
    kind: str,
    now: datetime,
    headers: Mapping[str, str],
    resets_at: str,
    resets_tz: str,
    down_streak: int,
) -> datetime | None:
    if kind == NEEDS_YOU:
        return None
    if kind == DOWN:
        return now + DOWN_STEPS[min(max(down_streak, 0), len(DOWN_STEPS) - 1)]
    wait = provider_wait(headers, now)
    if wait is not None:
        return now + min(max(wait - now, MIN_WAIT), MAX_WAIT)
    return next_reset(now, resets_at, resets_tz)


def limited_reason(now: datetime, until: datetime) -> str:
    if until - now <= SHORT_WAIT:
        return "The model asked CleanSync to slow down."
    return "The model's usage limit is used up."


def _message_of(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
        for key in ("message", "detail"):
            if isinstance(data.get(key), str):
                return data[key]
        if isinstance(err, str):
            return err
    return body


def clean_detail(body: str, secrets: Iterable[str] = ()) -> str | None:
    text = " ".join(_message_of((body or "")[:5000]).split())
    if not text:
        return None
    text = redact(text, extra=[s for s in secrets if s])
    for pat in _KEYISH:
        text = pat.sub(REMOVED, text)
    return text[:DETAIL_CHARS]
```

`redact` in `app/core/redact.py` masks with its own marker. If its mask differs from `[removed]`, the tests above still pass, because they only check that the secret is gone. But `test_clean_detail_takes_the_message_and_removes_keys` asserts `[removed]` appears. If `redact`'s token patterns already masked the `sk-` key with another marker, the `nvapi-` one is still removed by `_KEYISH`, so the assert holds. If ruff wants the long `_DURATION` line split, wrap it with implicit string concatenation.

- [ ] **Step 4: Run the tests to check they pass**

Run: `cd backend && uv run pytest tests/test_ai_limits.py -q && uv run ruff check app/services/ai/limits.py tests/test_ai_limits.py && uv run ruff format app/services/ai/limits.py tests/test_ai_limits.py`
Expected: all pass, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/limits.py backend/tests/test_ai_limits.py
git commit -m "feat(ai): work out what kind of failure a model reply is and how long to pause

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Saving the pause (migration 0010 and AiSettings)

**Files:**
- Create: `backend/app/database/migrations/versions/0010_model_limits.py`
- Modify: `backend/app/models/ai.py` (class `AiConnection`)
- Modify: `backend/app/services/ai/connections.py` (`AiTarget`, `clean_base_url`, `AiSettings`)
- Modify: `backend/app/services/rules/worker.py` (add `wake_waiting`)
- Test: `backend/tests/test_ai_connections.py`, `backend/tests/test_rules_worker.py`

**Interfaces:**
- Consumes (from Task 1): `Pause`, `DOWN`, `check_reset`.
- Produces:
  - `AiTarget` gains a last field: `skip_pause: bool = False`.
  - `AiSettings.pause_of(connection_id: int, now: datetime | None = None) -> Pause | None`. This is None when the connection isn't paused or its pause has ended.
  - `AiSettings.limits_of(connection_id: int) -> tuple[str, str, int]`, giving `(resets_at, resets_tz, down_streak)`.
  - `AiSettings.set_pause(connection_id: int, kind: str, until: datetime | None, reason: str, detail: str | None, now: datetime) -> Pause`. A down pause adds 1 to `down_streak`; other kinds leave it.
  - `AiSettings.clear_pause(connection_id: int) -> bool`. It sets every pause column to null and `down_streak` to 0, and returns True when anything was set.
  - `AiSettings.secrets_of(connection_id: int) -> list[str]`, the key and the header values.
  - `AiSettings.set_resets(connection_id: int, resets_at: str, resets_tz: str) -> AiConnection`. It raises `ConnectionError_` with `check_reset`'s message.
  - `AiSettings.create(..., resets_at: str = "00:00", resets_tz: str = "UTC")`.
  - `AiSettings.pause_for_task(task: AiTaskName) -> tuple[int, str, Pause] | None`, giving `(connection_id, connection_name, pause)` for the task's connection, after the same Writing rules → Sorting fallback that `target()` uses.
  - `AiSettings.update` clears the pause when the address, key or headers change. `AiSettings.set_task` clears the pause of the connection it sets.
  - `clean_base_url` removes a trailing `/chat/completions`.
  - `worker.wake_waiting(db: Session, now: datetime) -> int` sets `next_try_at = now` on every `RuleTodo` with `next_try_at > now`, and returns the count.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_ai_connections.py`:

```python
from datetime import UTC, datetime, timedelta

from app.services.ai.limits import DOWN, LIMITED, NEEDS_YOU

T0 = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


def test_new_connection_resets_at_utc_midnight_and_is_not_paused(ai):
    c = ai.create("Groq", "https://api.groq.com/openai/v1")
    assert (c.resets_at, c.resets_tz) == ("00:00", "UTC")
    assert ai.pause_of(c.id) is None and ai.limits_of(c.id) == ("00:00", "UTC", 0)


def test_chat_completions_is_removed_from_the_address(ai):
    c = ai.create("NIM", "https://integrate.api.nvidia.com/v1/chat/completions/")
    assert c.base_url == "https://integrate.api.nvidia.com/v1"


def test_pause_is_saved_and_survives_a_new_settings_object(ai, session_factory, layout):
    c = ai.create("Groq", "https://api.groq.com/openai/v1")
    ai.set_pause(c.id, LIMITED, T0 + timedelta(hours=14), "The model's usage limit is used up.", "Rate limit reached", T0)
    fresh = AiSettings(session_factory, layout.secrets_dir)  # as after a restart
    p = fresh.pause_of(c.id, now=T0 + timedelta(hours=1))
    assert p is not None and p.kind == LIMITED and p.detail == "Rate limit reached"
    assert p.until == T0 + timedelta(hours=14) and p.since == T0


def test_ended_pause_does_not_count(ai):
    c = ai.create("Groq", "https://api.groq.com/openai/v1")
    ai.set_pause(c.id, LIMITED, T0 + timedelta(minutes=5), "r", None, T0)
    assert ai.pause_of(c.id, now=T0 + timedelta(minutes=6)) is None


def test_needs_you_has_no_end(ai):
    c = ai.create("Groq", "https://api.groq.com/openai/v1")
    ai.set_pause(c.id, NEEDS_YOU, None, "The model's server refused the API key.", None, T0)
    assert ai.pause_of(c.id, now=T0 + timedelta(days=30)).kind == NEEDS_YOU


def test_down_streak_counts_and_clear_resets(ai):
    c = ai.create("Local", "http://ollama:11434/v1")
    ai.set_pause(c.id, DOWN, T0 + timedelta(minutes=1), "r", None, T0)
    ai.set_pause(c.id, DOWN, T0 + timedelta(minutes=5), "r", None, T0)
    assert ai.limits_of(c.id)[2] == 2
    assert ai.clear_pause(c.id) is True
    assert ai.limits_of(c.id)[2] == 0 and ai.pause_of(c.id) is None
    assert ai.clear_pause(c.id) is False


def test_set_resets_checks_the_values(ai):
    c = ai.create("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai")
    ai.set_resets(c.id, "00:00", "America/Los_Angeles")
    assert ai.limits_of(c.id)[:2] == ("00:00", "America/Los_Angeles")
    with pytest.raises(ConnectionError_):
        ai.set_resets(c.id, "7pm", "UTC")
    with pytest.raises(ConnectionError_):
        ai.set_resets(c.id, "00:00", "Nowhere/Land")


def test_changing_address_key_or_headers_clears_the_pause(ai):
    c = ai.create("Groq", "https://api.groq.com/openai/v1", api_key="gsk-old-key-123456")
    ai.set_pause(c.id, NEEDS_YOU, None, "The model's server refused the API key.", None, T0)
    ai.update(c.id, name="Groq 2")
    assert ai.pause_of(c.id) is not None  # a new name changes nothing
    ai.update(c.id, api_key="gsk-new-key-654321")
    assert ai.pause_of(c.id) is None


def test_saving_a_task_clears_its_connections_pause(ai):
    c = ai.create("Groq", "https://api.groq.com/openai/v1")
    ai.set_pause(c.id, NEEDS_YOU, None, "r", None, T0)
    ai.set_task(AiTaskName.SORTING, c.id, "llama-3.1-8b-instant")
    assert ai.pause_of(c.id) is None


def test_pause_for_task_follows_the_writing_rules_fallback(ai):
    c = ai.create("Groq", "https://api.groq.com/openai/v1")
    ai.set_task(AiTaskName.SORTING, c.id, "m")
    assert ai.pause_for_task(AiTaskName.SORTING) is None
    ai.set_pause(c.id, LIMITED, datetime.now(UTC) + timedelta(hours=1), "r", None, datetime.now(UTC))
    cid, name, p = ai.pause_for_task(AiTaskName.WRITING_RULES)
    assert (cid, name, p.kind) == (c.id, "Groq", LIMITED)


def test_secrets_of(ai):
    c = ai.create("GW", "https://gw.example.com/v1", api_key="sk-gw-123456789", headers={"X-T": "hv-secret-1"})
    assert sorted(ai.secrets_of(c.id)) == ["hv-secret-1", "sk-gw-123456789"]
```

Append to `backend/tests/test_rules_worker.py`:

```python
from app.services.rules.worker import wake_waiting


def test_wake_waiting_makes_later_rows_due(gbox):
    a, b = gbox.add("One"), gbox.add("Two")
    queue(gbox, a, b)
    now = utcnow()
    with gbox.sf() as db:
        db.get(RuleTodo, (gbox.acc.id, a.id)).next_try_at = now + timedelta(hours=5)
        db.commit()
        assert wake_waiting(db, now) == 1
        db.commit()
    assert all(t.next_try_at <= now + timedelta(seconds=1) for t in todos(gbox))
```

If `next_try_at` reads back as a naive datetime from SQLite, compare using `app.core.clock`'s `as_utc` helper, as `api/rules.py` does with `as_utc(ra.failing_since)`.

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_ai_connections.py tests/test_rules_worker.py -q`
Expected: FAIL. `AiConnection` has no `resets_at`, and `wake_waiting` can't be imported.

- [ ] **Step 3: Write the migration**

```python
# backend/app/database/migrations/versions/0010_model_limits.py
"""Model limits: when a connection's daily limit resets, and its saved pause

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("ai_connections") as b:
        b.add_column(sa.Column("resets_at", sa.String(5), nullable=False, server_default="00:00"))
        b.add_column(sa.Column("resets_tz", sa.Text(), nullable=False, server_default="UTC"))
        b.add_column(sa.Column("paused_until", TZ, nullable=True))
        b.add_column(sa.Column("pause_kind", sa.String(16), nullable=True))
        b.add_column(sa.Column("pause_reason", sa.Text(), nullable=True))
        b.add_column(sa.Column("pause_detail", sa.Text(), nullable=True))
        b.add_column(sa.Column("paused_at", TZ, nullable=True))
        b.add_column(sa.Column("down_streak", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("ai_connections") as b:
        for col in ("down_streak", "paused_at", "pause_detail", "pause_reason", "pause_kind",
                    "paused_until", "resets_tz", "resets_at"):
            b.drop_column(col)
```

- [ ] **Step 4: Add the model columns**

In `backend/app/models/ai.py`, inside `class AiConnection`, after `modes`:

```python
    # Model limits spec §6: when the daily limit resets, and the saved pause (none: not paused).
    resets_at: Mapped[str] = mapped_column(String(5), default="00:00", server_default="00:00")
    resets_tz: Mapped[str] = mapped_column(Text, default="UTC", server_default="UTC")
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pause_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pause_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    down_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
```

Add `String` to the file's `sqlalchemy` import if it is missing. Check that the repo's "models match migrations" test passes. It's in `tests/test_database.py`; run it in Step 7.

- [ ] **Step 5: Extend `connections.py`**

1. In `AiTarget`, add a last field: `skip_pause: bool = False  # Test: call even while paused`.
2. In `clean_base_url`, before the `return`:

```python
    if url.lower().endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")
```

3. In `AiSettings.create`, add the keyword parameters `resets_at: str = "00:00", resets_tz: str = "UTC"`. Call `check_reset(resets_at, resets_tz)` (turning `ValueError` into `ConnectionError_(str(e))`) before saving, and set both on the new row.
4. In `AiSettings.update`, track `server_changed = False`. Set it to True when `base_url` changes (any change of the URL string), when `api_key` isn't `KEEP`, and when `headers` isn't `KEEP`. Before `db.commit()`, if it is True, call `_clear(conn)`.
5. In `set_task`, after `row.connection_id = connection_id`, if `connection_id is not None`, call `_clear(db.get(AiConnection, connection_id))`.
6. Add these methods and the helper (import `datetime` and `utcnow`, `as_utc` from `app.core.clock`, and `Pause`, `DOWN`, `check_reset` from `app.services.ai.limits`):

```python
def _clear(conn: AiConnection) -> bool:
    had = conn.pause_kind is not None or (conn.down_streak or 0) > 0
    conn.paused_until = conn.pause_kind = conn.pause_reason = None
    conn.pause_detail = conn.paused_at = None
    conn.down_streak = 0
    return had

    # ---- pauses (model limits spec §6-7): saved here, never only in memory --------------
    def pause_of(self, connection_id: int, now: datetime | None = None) -> Pause | None:
        now = now or utcnow()
        with self.sf() as db:
            c = db.get(AiConnection, connection_id)
            if c is None or c.pause_kind is None:
                return None
            until = as_utc(c.paused_until) if c.paused_until else None
            if until is not None and until <= now:
                return None
            return Pause(c.pause_kind, until, c.pause_reason or "", c.pause_detail,
                         as_utc(c.paused_at) if c.paused_at else now)

    def limits_of(self, connection_id: int) -> tuple[str, str, int]:
        with self.sf() as db:
            c = db.get(AiConnection, connection_id)
            if c is None:
                return "00:00", "UTC", 0
            return c.resets_at or "00:00", c.resets_tz or "UTC", c.down_streak or 0

    def set_pause(self, connection_id: int, kind: str, until: datetime | None, reason: str,
                  detail: str | None, now: datetime) -> Pause:
        with self.sf() as db:
            c = db.get(AiConnection, connection_id)
            if c is not None:
                c.pause_kind, c.paused_until, c.pause_reason = kind, until, reason
                c.pause_detail, c.paused_at = detail, now
                if kind == DOWN:
                    c.down_streak = (c.down_streak or 0) + 1
                db.commit()
        return Pause(kind, until, reason, detail, now)

    def clear_pause(self, connection_id: int) -> bool:
        with self.sf() as db:
            c = db.get(AiConnection, connection_id)
            if c is None or not _clear(c):
                return False
            db.commit()
            return True

    def secrets_of(self, connection_id: int) -> list[str]:
        key = self._read_key(connection_id)
        return [*([key] if key else []), *self._read_headers(connection_id).values()]

    def set_resets(self, connection_id: int, resets_at: str, resets_tz: str) -> AiConnection:
        try:
            check_reset(resets_at, resets_tz)
        except ValueError as e:
            raise ConnectionError_(str(e)) from None
        with self.sf() as db:
            conn = self._get(db, connection_id)
            conn.resets_at, conn.resets_tz = resets_at, resets_tz
            db.commit()
            return conn

    def pause_for_task(self, task: AiTaskName) -> tuple[int, str, Pause] | None:
        t = self.target(task)
        if t is None:
            return None
        p = self.pause_of(t.connection_id)
        if p is None:
            return None
        with self.sf() as db:
            c = db.get(AiConnection, t.connection_id)
            return (t.connection_id, c.name if c else "", p)
```

`_clear` is a module-level function placed above `class AiSettings`. `clear_pause` does an UPDATE only when something is set. That matters because the client calls it after every call that works (Task 3), and most of the time nothing is set. If `AiConnection` is expired after commit and `return conn` in `set_resets` trips a DetachedInstanceError, follow whatever `update()` already does: it returns `conn` after commit, so the session factory uses `expire_on_commit=False`.

- [ ] **Step 6: Add `wake_waiting` to the worker module**

In `backend/app/services/rules/worker.py`, after `enqueue_new`:

```python
def wake_waiting(db: Session, now) -> int:
    """Resume now (model limits spec §9): every to-do row waiting for later is due now."""
    return (
        db.execute(
            update(RuleTodo)
            .where(RuleTodo.next_try_at > now)
            .values(next_try_at=now)
            .execution_options(synchronize_session=False)
        ).rowcount
        or 0
    )
```

- [ ] **Step 7: Run the tests**

Run: `cd backend && uv run pytest tests/test_ai_connections.py tests/test_rules_worker.py tests/test_database.py -q && uv run ruff check . && uv run ruff format .`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/database/migrations/versions/0010_model_limits.py backend/app/models/ai.py backend/app/services/ai/connections.py backend/app/services/rules/worker.py backend/tests/test_ai_connections.py backend/tests/test_rules_worker.py
git commit -m "feat(ai): save a connection's pause and daily reset time in the database

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The gate in the AI client

**Files:**
- Modify: `backend/app/services/ai/client.py`
- Modify: `backend/app/main.py:370`
- Test: `backend/tests/test_ai_client.py` (new tests; existing ones must keep passing unchanged), `backend/tests/test_rules_wiring.py`

**Interfaces:**
- Consumes (Task 1): `kind_of`, `has_limit_words`, `pause_end`, `limited_reason`, `clean_detail`, `Pause`, `LIMITED`, `DOWN`, `NEEDS_YOU`.
- Consumes (Task 2): the `AiSettings` methods `pause_of`, `limits_of`, `set_pause`, `clear_pause`, `secrets_of`, and `AiTarget.skip_pause`.
- Produces:
  - `class AiPaused(AiError)`, with attribute `pause: Pause`. `str(e)` gives:
    - limited and down: `f"{reason} CleanSync will try again after {until:%Y-%m-%d %H:%M} UTC."`
    - needs_you: `f"{reason} Check Settings → Models."`
    - When the detail is set, `f" The server said: {detail}"` is appended.
  - `class LimitStore(Protocol)`, with the five methods above.
  - `AiClient(modes, *, limits: LimitStore | None = None, http_factory=None, monotonic=..., retry_pause_s=2.0, clock=utcnow)`.
  - `AiClient.ask(...)` with `limits` set:
    - raises `AiPaused` before any HTTP call when `pause_of` returns a pause and `target.skip_pause` is False;
    - after a failure, saves a pause and raises `AiPaused`;
    - after an answer that works, calls `clear_pause`.
  - With `limits=None`, `ask` keeps today's behaviour, including the old exception types.
  - `list_models` is unchanged: no gate, no pause.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_ai_client.py`:

```python
from datetime import UTC, datetime, timedelta

from app.services.ai.client import AiPaused
from app.services.ai.limits import DOWN, LIMITED, NEEDS_YOU, Pause

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
        return_value=httpx.Response(429, headers={"Retry-After": "3600"},
                                    json={"error": {"message": "Rate limit reached for requests per day"}})
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
    [(403, "rate limit for this key", LIMITED), (403, "forbidden", NEEDS_YOU),
     (401, "", NEEDS_YOU), (405, "", NEEDS_YOU)],
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
    import dataclasses

    respx.post(f"{URL}/chat/completions").mock(return_value=reply('{"rule": 1}'))
    limits = Limits(pause=Pause(NEEDS_YOU, None, "r", None, NOW))
    ans = await ask(limited_client(limits), dataclasses.replace(target(), skip_pause=True))
    assert ans.data == {"rule": 1} and limits.pause is None


@respx.mock
async def test_detail_never_carries_the_key():
    respx.post(f"{URL}/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": "limit for key sk-live-abcdef123456"}})
    )
    limits = Limits(secrets=["sk-live-abcdef123456"])
    with pytest.raises(AiPaused) as e:
        await ask(limited_client(limits), target("sk-live-abcdef123456"))
    assert "sk-live-abcdef123456" not in str(e.value) and "sk-live" not in (e.value.pause.detail or "")


def test_paused_message_shapes():
    lim = AiPaused(Pause(LIMITED, NOW, "The model's usage limit is used up.", None, NOW))
    assert str(lim) == "The model's usage limit is used up. CleanSync will try again after 2026-10-13 10:00 UTC."
    you = AiPaused(Pause(NEEDS_YOU, None, "The model's server refused the API key.", "bad key", NOW))
    assert str(you) == "The model's server refused the API key. Check Settings → Models. The server said: bad key"
```

Append to `backend/tests/test_rules_wiring.py`, using that file's existing `app` fixture:

```python
def test_the_ai_client_saves_pauses_in_the_settings(app):
    assert app.state.ai_client.limits is app.state.ai
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_ai_client.py tests/test_rules_wiring.py -q`
Expected: FAIL with `ImportError: cannot import name 'AiPaused'`.

- [ ] **Step 3: Change the client**

In `backend/app/services/ai/client.py`:

1. Imports: `from app.core.clock import utcnow`, and `from app.services.ai.limits import DOWN, LIMITED, NEEDS_YOU, Pause, clean_detail, has_limit_words, kind_of, limited_reason, pause_end`.
2. After `AiRefused`, add:

```python
class AiPaused(AiError):
    """The connection is paused (model limits spec §7): no call was made, or the call just
    made paused it. The pause is saved in the database."""

    def __init__(self, pause: Pause) -> None:
        self.pause = pause
        if pause.until is not None:
            text = f"{pause.reason} CleanSync will try again after {pause.until:%Y-%m-%d %H:%M} UTC."
        else:
            text = f"{pause.reason} Check Settings → Models."
        if pause.detail:
            text += f" The server said: {pause.detail}"
        super().__init__(text)


class LimitStore(Protocol):
    def pause_of(self, connection_id: int, now=None) -> Pause | None: ...
    def limits_of(self, connection_id: int) -> tuple[str, str, int]: ...
    def set_pause(self, connection_id: int, kind: str, until, reason: str, detail, now) -> Pause: ...
    def clear_pause(self, connection_id: int) -> bool: ...
    def secrets_of(self, connection_id: int) -> list[str]: ...


class _Failed(Exception):
    """One call failed: what kind, CleanSync's sentence, and the reply to read limits from."""

    def __init__(self, kind: str, message: str, headers=None, body: str = "") -> None:
        super().__init__(message)
        self.kind, self.message = kind, message
        self.headers, self.body = dict(headers or {}), body
```

3. In `AiClient.__init__`, add the keyword parameters `limits: LimitStore | None = None` and `clock: Callable[[], datetime] = utcnow` (import `datetime`). Store them as `self.limits` and `self.clock`.
4. Rewrite `_post` so it raises `_Failed` instead of `AiRefused`/`AiUnreachable`. Keep the order of checks below exactly:

```python
    async def _post(self, t: AiTarget, body: dict) -> dict:
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
                if code >= 500:
                    last = f"the model's server is failing (HTTP {code})"
                elif code >= 400:
                    raise _Failed(
                        NEEDS_YOU, f"The model's server refused the request (HTTP {code}).", body=text
                    )
                else:
                    try:
                        return resp.json()
                    except ValueError:
                        last = "the model's server sent something that isn't JSON"
            if attempt == 1:
                await asyncio.sleep(self.retry_pause_s)
        raise _Failed(DOWN, last[0].upper() + last[1:] + ".")
```

The `429 → busy` branch is gone: 429 is limited now, caught by the first check.

5. Rename the current `ask` body to `async def _ask(self, target, *, system, user, schema, schema_name, parse_text) -> AiAnswer`, unchanged except the last line: `raise _Failed(NEEDS_YOU, "The model's server refused every way of asking.")`. Then add the new public `ask`:

```python
    async def ask(self, target: AiTarget, *, system: str, user: str, schema: dict,
                  schema_name: str, parse_text: Callable[[str], dict | None]) -> AiAnswer:
        kw = dict(system=system, user=user, schema=schema, schema_name=schema_name,
                  parse_text=parse_text)
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
            raise AiPaused(self._pause(cid, f)) from None
        self.limits.clear_pause(cid)
        return answer

    def _pause(self, cid: int, f: _Failed) -> Pause:
        now = self.clock()
        resets_at, resets_tz, streak = self.limits.limits_of(cid)
        until = pause_end(f.kind, now, f.headers, resets_at, resets_tz, streak)
        reason = limited_reason(now, until) if f.kind == LIMITED else f.message
        detail = clean_detail(f.body, self.limits.secrets_of(cid)) if f.body else None
        return self.limits.set_pause(cid, f.kind, until, reason, detail, now)
```

`_ask` is the old loop. Its `except _ShapeRefused: continue` stays, and `_Failed` passes through it. `has_limit_words` isn't used in client.py once `kind_of` does the check, so drop it from the import if ruff flags it.

6. In `backend/app/main.py:370`, use `app.state.ai_client = AiClient(app.state.ai, limits=app.state.ai)`.

- [ ] **Step 4: Run all AI tests**

Run: `cd backend && uv run pytest tests/test_ai_client.py tests/test_ai_api.py tests/test_fake_model.py tests/test_rules_wiring.py tests/test_rules_chooser.py tests/test_rules_drafting.py -q`
Expected: all pass. The old client tests use `Modes()` without `limits`, so they keep the old exceptions. One assumption to check: no existing test relies on a 429 being retried. If one does (search `429` in `tests/test_ai_client.py`), change it to expect a single call. That is the spec's change, so note it in your report.

- [ ] **Step 5: Run the full backend suite and lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all pass.

`tests/test_ai_api.py` uses the real app, whose client now has `limits`. A failing **Test** there now gets `AiPaused` (an `AiError`), and the endpoint still returns `ok=False, error=str(e)`. Assertions on the exact error text may need the new wording. Update them to check the `startswith` of the reason, and say so in the report.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ai/client.py backend/app/main.py backend/tests/test_ai_client.py backend/tests/test_rules_wiring.py backend/tests/test_ai_api.py
git commit -m "feat(ai): a limit, outage or refused key pauses the connection; a paused one isn't called

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Sorting, runs and drafting wait on a pause

**Files:**
- Modify: `backend/app/services/rules/worker.py` (`_decide_one`)
- Modify: `backend/app/services/rules/drafting.py` (`sentence_to_rule`, `rule_from_mails`)
- Test: `backend/tests/test_rules_worker.py`, `backend/tests/test_rules_drafting.py`

**Interfaces:**
- Consumes: `AiPaused` and `Pause` (Task 3), `wake_waiting` (Task 2).
- Produces:
  - **Worker:** on `AiPaused`, the mail's to-do row keeps `attempts` and `last_error`, and gets `next_try_at = pause.until` (or now + 1 hour when `until` is None). `failing_since` is not set, and the batch goes on to the next mail.
  - **Drafting:** on `AiPaused`, return the same draft as with no model, with the note `str(e)` in place of `NO_MODEL_NOTE`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_rules_worker.py`:

```python
from app.services.ai.client import AiPaused
from app.services.ai.limits import LIMITED, NEEDS_YOU, Pause
from app.services.rules.store import update_rule  # if the name differs, use the store's edit function
from app.models.enums import RuleCombine


def _paused(until):
    return AiPaused(Pause(LIMITED if until else NEEDS_YOU, until, "The model's usage limit is used up.", None, utcnow()))


async def test_paused_model_keeps_mail_waiting_without_counting_a_try(gbox):
    _rule(gbox)
    a, b = gbox.add("One"), gbox.add("Two")
    queue(gbox, a, b)
    until = utcnow() + timedelta(hours=10)
    pick = Pick(error=_paused(until))
    assert await worker(gbox, pick).run_once(gbox.acc.id) == 0
    assert pick.calls == 2  # the batch goes on: the gate refuses each one at no cost
    for t in todos(gbox):
        assert t.attempts == 0 and t.last_error is None
        assert abs((as_utc(t.next_try_at) - until).total_seconds()) < 1
    with gbox.sf() as db:
        assert db.get(RuleAccount, gbox.acc.id).failing_since is None
    assert decisions(gbox) == []


async def test_needs_you_pause_waits_an_hour(gbox):
    _rule(gbox)
    a = gbox.add("One")
    queue(gbox, a)
    before = utcnow()
    await worker(gbox, Pick(error=_paused(None))).run_once(gbox.acc.id)
    (t,) = todos(gbox)
    assert as_utc(t.next_try_at) >= before + timedelta(minutes=59)


async def test_fixed_only_rule_still_sorts_while_paused(gbox):
    with gbox.sf() as db:
        create_rule(
            db, gbox.acc.id,
            RuleIn(name="Shop", description="d", actions=[{"kind": "archive"}],
                   conditions={"subject_contains": ["Receipt"]}, combine=RuleCombine.FIXED_ONLY),
            "a",
        )
        create_rule(db, gbox.acc.id, RuleIn(name="AI", description="d", actions=[{"kind": "star"}]), "a")
    receipt, other = gbox.add("Receipt 42"), gbox.add("Hello")
    queue(gbox, receipt, other)
    await worker(gbox, Pick(error=_paused(utcnow() + timedelta(hours=1)))).run_once(gbox.acc.id)
    (d,) = decisions(gbox)
    assert d.message_id == receipt.id and d.rule_name == "Shop"
    assert [t.message_id for t in todos(gbox)] == [other.id]


async def test_queue_drains_after_the_pause_ends(gbox):
    _rule(gbox)
    a = gbox.add("One")
    queue(gbox, a)
    await worker(gbox, Pick(error=_paused(utcnow() + timedelta(hours=1)))).run_once(gbox.acc.id)
    with gbox.sf() as db:
        wake_waiting(db, utcnow())  # as Resume now does, or the clock passing the end
        db.commit()
    assert await worker(gbox, Pick()).run_once(gbox.acc.id) == 1
    assert todos(gbox) == []
```

`_rule(gbox, **kw)` already exists in that file at line 153. Use it, check its signature, and pass what it needs. Import `as_utc` from `app.core.clock`. Drop the `update_rule` import if it isn't used. If `RuleIn` names the fields differently (`conditions`, `combine`), read `app/services/rules/store.py` and use its names.

Append to `backend/tests/test_rules_drafting.py`:

```python
from datetime import UTC, datetime, timedelta

from app.services.ai.client import AiPaused
from app.services.ai.limits import LIMITED, Pause


class PausedClient:
    async def ask(self, *a, **kw):
        now = datetime(2026, 10, 13, tzinfo=UTC)
        raise AiPaused(Pause(LIMITED, now + timedelta(hours=3), "The model's usage limit is used up.", None, now))


async def test_sentence_while_paused_opens_the_form_with_a_note(some_target):
    d = await drafting.sentence_to_rule(PausedClient(), some_target, "Archive receipts from shop.example.com")
    assert d.description == "Archive receipts from shop.example.com"
    assert d.notes and "usage limit is used up" in d.notes[0]


async def test_mails_while_paused_keep_the_shared_conditions(some_target, some_mails):
    d = await drafting.rule_from_mails(PausedClient(), some_target, some_mails)
    assert d.conditions and "usage limit is used up" in d.notes[0]
```

Use whatever target and mail helpers `tests/test_rules_drafting.py` already has. Read its top first. `some_target` and `some_mails` stand for those helpers; replace them with the real names and don't add new fixtures if equivalents exist.

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_rules_worker.py tests/test_rules_drafting.py -q`
Expected: the new tests FAIL. The worker counts a try and sets `failing_since`, and drafting lets `AiPaused` escape.

- [ ] **Step 3: Handle `AiPaused` in the worker**

In `worker.py`, import `AiPaused` next to `AiError`. In `_decide_one`, put this before the existing `except AiError as e:` branch:

```python
        except AiPaused as e:
            # Model limits spec §8: a paused model costs the mail nothing. It waits for the
            # pause to end and the batch goes on, since the next mail may need no model.
            until = e.pause.until or now + timedelta(hours=1)
            with self.sf() as db:
                db.execute(
                    update(RuleTodo)
                    .where(*self._mine(account_id, message_id, job_id))
                    .values(next_try_at=until)
                    .execution_options(synchronize_session=False)
                )
                db.commit()
            return 0, True, False
```

- [ ] **Step 4: Handle `AiPaused` in drafting**

In `drafting.py`, import `AiPaused`. In `sentence_to_rule`, wrap the `client.ask(...)` call:

```python
    try:
        ans = await client.ask(...)  # unchanged arguments
    except AiPaused as e:
        return RuleDraft(_short_name(sentence), sentence, {}, RuleCombine.AI_ONLY, [], [str(e)])
```

In `rule_from_mails`, do the same with `RuleDraft(name or "New rule", "", conditions, _combine(conditions), [], [str(e)])`.

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/rules/worker.py backend/app/services/rules/drafting.py backend/tests/test_rules_worker.py backend/tests/test_rules_drafting.py
git commit -m "feat(rules): mail waits for a paused model without using up a try

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: API: pause, reset time, Resume now, Test

**Files:**
- Modify: `backend/app/schemas/ai.py`, `backend/app/schemas/rules.py`
- Modify: `backend/app/api/models_ai.py`, `backend/app/api/rules.py`
- Test: `backend/tests/test_ai_api.py`, `backend/tests/test_rules_api.py`

**Interfaces:**
- Consumes: the Task 2 `AiSettings` methods, `AiPaused` (Task 3), `wake_waiting` (Task 2).
- Produces (the frontend in Task 6 relies on these exact names):
  - `PauseOut { kind: str; until: UtcDatetime | None; reason: str; detail: str | None; since: UtcDatetime; connection_id: int; connection_name: str }`, in `app/schemas/ai.py`.
  - `ConnectionOut` adds `resets_at: str`, `resets_tz: str`, `pause: PauseOut | None`.
  - `ConnectionIn` adds `resets_at: str = "00:00"` and `resets_tz: str = Field("UTC", max_length=64)`. `ConnectionPatch` adds `resets_at: str | None` and `resets_tz: str | None`.
  - `POST /api/ai/connections/{conn_id}/resume` returns `ConnectionOut`. It clears the pause, calls `wake_waiting`, commits, and pokes `app.state.rule_worker`.
  - `TestOut` adds `detail: str | None = None`. Test uses `skip_pause=True`.
  - `SortingOut` adds `paused: PauseOut | None`, for the Sorting task's connection.
  - `JobPreviewOut` adds `paused: PauseOut | None = None`, filled in by the API from the Sorting task's connection.
  - `POST /api/rules/try` turns `AiPaused` into HTTP 503 with `str(e)` as the detail.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_ai_api.py`:

```python
from datetime import timedelta

from app.core.clock import utcnow
from app.services.ai.limits import LIMITED, NEEDS_YOU


def test_connection_shows_reset_time_and_no_pause(auth_client):
    c = make(auth_client)
    assert (c["resets_at"], c["resets_tz"], c["pause"]) == ("00:00", "UTC", None)


def test_patch_reset_time_and_bad_zone(auth_client):
    c = make(auth_client)
    r = auth_client.patch(f"/api/ai/connections/{c['id']}", json={"resets_at": "00:00", "resets_tz": "America/Los_Angeles"})
    assert r.status_code == 200 and r.json()["resets_tz"] == "America/Los_Angeles"
    bad = auth_client.patch(f"/api/ai/connections/{c['id']}", json={"resets_tz": "Nowhere/Land"})
    assert bad.status_code == 400 and "time zone" in bad.json()["detail"]


def test_pause_is_listed_and_resume_clears_it(auth_client, app):
    c = make(auth_client, api_key="sk-api-secret-1")
    app.state.ai.set_pause(c["id"], LIMITED, utcnow() + timedelta(hours=5), "The model's usage limit is used up.", "Rate limit reached", utcnow())
    listed = auth_client.get("/api/ai/connections").json()[0]
    assert listed["pause"]["kind"] == LIMITED and listed["pause"]["detail"] == "Rate limit reached"
    assert listed["pause"]["connection_name"] == "Ollama"
    r = auth_client.post(f"/api/ai/connections/{c['id']}/resume")
    assert r.status_code == 200 and r.json()["pause"] is None
    assert auth_client.post("/api/ai/connections/9999/resume").status_code == 404


@respx.mock
def test_test_button_ignores_a_pause_and_clears_it_on_success(auth_client, app):
    c = make(auth_client)
    app.state.ai.set_pause(c["id"], NEEDS_YOU, None, "The model's server refused the API key.", None, utcnow())
    respx.post(f"{URL}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": '{"rule": 2, "why": "digest"}'}}]})
    )
    r = auth_client.post("/api/ai/test", json={"connection_id": c["id"], "model": "llama3.1:8b"})
    assert r.json()["ok"] is True
    assert app.state.ai.pause_of(c["id"]) is None


@respx.mock
def test_test_button_shows_the_providers_message(auth_client):
    c = make(auth_client, api_key="sk-api-secret-1")
    respx.post(f"{URL}/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": "Daily limit hit for sk-api-secret-1"}})
    )
    r = auth_client.post("/api/ai/test", json={"connection_id": c["id"], "model": "llama3.1:8b"}).json()
    assert r["ok"] is False and r["detail"].startswith("Daily limit hit")
    assert "sk-api-secret-1" not in str(r)
```

Append to `backend/tests/test_rules_api.py`. Use that file's existing account fixture or helper; read its top for the name, since `acc_id` below stands for it.

```python
def test_sorting_status_shows_the_sorting_connections_pause(auth_client, app, acc_id):
    from datetime import timedelta

    from app.core.clock import utcnow
    from app.models.enums import AiTaskName
    from app.services.ai.limits import LIMITED

    conn = app.state.ai.create("Groq", "https://api.groq.com/openai/v1")
    app.state.ai.set_task(AiTaskName.SORTING, conn.id, "m")
    assert auth_client.get(f"/api/rules/sorting?account_id={acc_id}").json()["paused"] is None
    app.state.ai.set_pause(conn.id, LIMITED, utcnow() + timedelta(hours=2), "The model's usage limit is used up.", None, utcnow())
    p = auth_client.get(f"/api/rules/sorting?account_id={acc_id}").json()["paused"]
    assert p["kind"] == LIMITED and p["connection_name"] == "Groq"
```

Also add a test that a rule job's preview carries `paused`. Copy how the existing job tests in `test_rules_api.py` start a job, then set a pause as above and assert `GET /api/rule-jobs/{id}` has `paused.kind == "limited"`.

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd backend && uv run pytest tests/test_ai_api.py tests/test_rules_api.py -q`
Expected: the new tests FAIL with `KeyError: 'resets_at'`, a 404 on resume, and so on.

- [ ] **Step 3: Schemas**

In `backend/app/schemas/ai.py`, import `UtcDatetime` from `app.schemas.common` and add:

```python
class PauseOut(BaseModel):
    kind: str
    until: UtcDatetime | None
    reason: str
    detail: str | None
    since: UtcDatetime
    connection_id: int
    connection_name: str
```

Then:
- `ConnectionIn`: add `resets_at: str = Field("00:00", max_length=5)` and `resets_tz: str = Field("UTC", max_length=64)`.
- `ConnectionPatch`: add `resets_at: str | None = Field(None, max_length=5)` and `resets_tz: str | None = Field(None, max_length=64)`.
- `ConnectionOut`: add `resets_at: str`, `resets_tz: str`, `pause: PauseOut | None = None`.
- `TestOut`: add `detail: str | None = None`.

In `backend/app/schemas/rules.py`, import `PauseOut` from `app.schemas.ai`. Add `paused: PauseOut | None = None` to `SortingOut` and to `JobPreviewOut`.

- [ ] **Step 4: Models API**

In `backend/app/api/models_ai.py`:

```python
def _pause_out(conn_id: int, name: str, p) -> PauseOut | None:
    if p is None:
        return None
    return PauseOut(kind=p.kind, until=p.until, reason=p.reason, detail=p.detail, since=p.since,
                    connection_id=conn_id, connection_name=name)
```

1. `_out(c)` becomes `_out(request, c)`. It adds `resets_at=c.resets_at, resets_tz=c.resets_tz, pause=_pause_out(c.id, c.name, _ai(request).pause_of(c.id))`. Update every caller.
2. `create`: pass `resets_at=body.resets_at, resets_tz=body.resets_tz` to `_ai(request).create`.
3. `update`: after `_ai(request).update(...)`, if `"resets_at" in given or "resets_tz" in given`, read the saved values (`limits_of`), then call `set_resets(conn_id, body.resets_at or saved_at, body.resets_tz or saved_tz)`. Both raise `ConnectionError_`, which is already turned into a 400.
4. New endpoint:

```python
@router.post("/connections/{conn_id}/resume", response_model=ConnectionOut)
def resume(conn_id: int, request: Request, ctx: AuthContext = Depends(require_user)):
    ai = _ai(request)
    conn = next((c for c in ai.connections() if c.id == conn_id), None)
    if conn is None:
        raise HTTPException(404, "That connection isn't saved.")
    ai.clear_pause(conn_id)
    with request.app.state.session_factory() as db:
        wake_waiting(db, utcnow())
        db.commit()
    request.app.state.rule_worker.poke()
    return _out(request, conn)
```

Use the session factory name `main.py` stores on `app.state`. Grep `app.state.session_factory`; if it is named differently, use that name. Import `wake_waiting` from `app.services.rules.worker` and `utcnow` from `app.core.clock`.

5. `test` endpoint: build the target, then `target = dataclasses.replace(target, skip_pause=True)`. In `except AiError as e:` return `TestOut(ok=False, error=e.pause.reason if isinstance(e, AiPaused) else str(e), detail=e.pause.detail if isinstance(e, AiPaused) else None)`.

- [ ] **Step 5: Rules API**

In `backend/app/api/rules.py`:
1. In `_sorting_out`, add `paused=_sorting_pause(request)` with:

```python
def _sorting_pause(request: Request) -> PauseOut | None:
    found = request.app.state.ai.pause_for_task(AiTaskName.SORTING)
    if found is None:
        return None
    cid, name, p = found
    return PauseOut(kind=p.kind, until=p.until, reason=p.reason, detail=p.detail, since=p.since,
                    connection_id=cid, connection_name=name)
```

2. Wherever a `JobPreviewOut(**asdict(...))` is built (the `/api/rule-jobs/open` and `/api/rule-jobs/{job_id}` handlers), add `paused=_sorting_pause(request)` when the job state is `PREVIEWING`, otherwise `None`.
3. In `_problem`, check `AiPaused` before `AiError`: `return HTTPException(503, str(e))`. The `try_rule` handler already routes `AiError` through `_problem`.
4. Import `AiPaused` and `PauseOut`.

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all pass. `tests/test_api_docs.py` may check that every route is documented. If it fails on the new resume route, add it the way that test asks.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas backend/app/api backend/tests/test_ai_api.py backend/tests/test_rules_api.py
git commit -m "feat(api): show a connection's pause and reset time; Resume now; Test shows the server's message

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Frontend: settings, status line, run dialog

**Files:**
- Modify: `frontend/src/api/rules.ts` (types)
- Create: `frontend/src/lib/pause.ts`
- Modify: `frontend/src/hooks/useRules.ts` (resume hook; Test refreshes connections)
- Modify: `frontend/src/components/settings/ModelsSettings.tsx`
- Modify: `frontend/src/components/MailBackupStatus.tsx` (`SortingStatus`)
- Modify: `frontend/src/components/mail/RunRulesDialog.tsx`
- Test: `frontend/src/lib/pause.test.ts`, `frontend/src/components/settings/ModelsSettings.test.tsx`, plus the existing tests next to `MailBackupStatus.tsx` and `RunRulesDialog.tsx` (find them with `ls frontend/src/components/**/*.test.tsx`)

**Interfaces:**
- Consumes: the Task 5 API shapes, using the exact field names.
- Produces:
  - `type AiPause = { kind: "limited" | "down" | "needs_you"; until: string | null; reason: string; detail: string | null; since: string; connection_id: number; connection_name: string }`.
  - `AiConnection` adds `resets_at: string; resets_tz: string; pause: AiPause | null`.
  - `Sorting` adds `paused: AiPause | null`. `JobPreview` adds `paused?: AiPause | null`. `AiTest` adds `detail?: string | null`.
  - `pauseLine(p: AiPause, now?: Date): string` in `lib/pause.ts`.
  - `useResumeConnection()`, which posts to `/api/ai/connections/${id}/resume` with keys `["ai-connections", "ai-tasks", "rules-sorting"]`.

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/src/lib/pause.test.ts
import { describe, expect, it } from "vitest";

import { pauseLine } from "./pause";

const base = { reason: "The model's usage limit is used up.", detail: null, since: "2026-10-13T10:00:00Z", connection_id: 1, connection_name: "Groq" };
const now = new Date("2026-10-13T12:00:00Z");

describe("pauseLine", () => {
  it("says until when a limited connection is paused", () => {
    const s = pauseLine({ ...base, kind: "limited", until: "2026-10-14T00:00:00Z" }, now);
    expect(s).toMatch(/^Groq is paused until .+: the model's usage limit is used up\.$/);
  });
  it("says when an outage is tried again", () => {
    expect(pauseLine({ ...base, kind: "down", reason: "Couldn't reach x.", until: "2026-10-13T12:05:00Z" }, now)).toMatch(
      /^Groq isn't answering \(Couldn't reach x\.\)\. CleanSync tries again at .+\.$/,
    );
  });
  it("points to the settings when the owner must act", () => {
    expect(pauseLine({ ...base, kind: "needs_you", reason: "The model's server refused the API key.", until: null }, now)).toBe(
      "Groq stopped: the model's server refused the API key. Check Settings → Models.",
    );
  });
});
```

Add to `ModelsSettings.test.tsx`, following how that file already mocks `/api/ai/connections` and renders:
- A connection with `pause: { kind: "limited", until: <tomorrow>, reason: "The model's usage limit is used up.", detail: "Rate limit reached for requests per day", ... }` shows the text `Paused until`, a **Show the server's message** toggle that reveals the detail, and a **Resume now** button. Clicking it posts to `/api/ai/connections/1/resume`.
- The connection dialog has **Daily limit resets at** (`type="time"`) and **Time zone** fields. Saving sends `resets_at` and `resets_tz`.
- A failed Test whose reply has `detail` shows both `error` and `The server said: <detail>`.

Add to the status line tests (the file testing `MailBackupStatus` or `SortingStatus`):
- `sorting.paused` set gives a warn notice containing `Sorting is waiting.`, `pauseLine(...)`, `412 mails waiting` and a **Settings → Models** link. This replaces the counts line, and takes priority over `warning`.

Add to the `RunRulesDialog` tests:
- A `PREVIEWING` job with `paused` shows `pauseLine(paused)` below the progress bar.

- [ ] **Step 2: Run the tests to check they fail**

Run: `cd frontend && npm test -- --run src/lib/pause.test.ts src/components`
Expected: FAIL. `./pause` doesn't exist, and the texts aren't found.

- [ ] **Step 3: Types, the line, and the hook**

`frontend/src/lib/pause.ts`:

```ts
import type { AiPause } from "../api/rules";
import { formatWhen } from "./format";

const lower = (s: string) => (s ? s[0].toLowerCase() + s.slice(1) : s);

/** One plain line about a paused model connection (model limits spec §9). */
export function pauseLine(p: AiPause, now = new Date()): string {
  const who = p.connection_name || "The model";
  if (p.kind === "needs_you") return `${who} stopped: ${lower(p.reason)} Check Settings → Models.`;
  const when = formatWhen(p.until, now);
  if (p.kind === "down") return `${who} isn't answering (${p.reason}). CleanSync tries again at ${when}.`;
  return `${who} is paused until ${when}: ${lower(p.reason)}`;
}
```

In `frontend/src/api/rules.ts`, add `AiPause` and the fields listed under Produces. In `frontend/src/hooks/useRules.ts`:

```ts
export const useResumeConnection = () =>
  useChange((b: { id: number }) => post<AiConnection>(`/api/ai/connections/${b.id}/resume`, {}), { keys: [...AI, "rule-job", "rule-job-open"] });
```

Change `useTestModel` so its `keys` is `AI` instead of `[]`. A Test can start or clear a pause, so the connection list must refresh. Keep `quiet: true`. Check `post`'s signature in `api/client.ts`; if it takes no body, call it without `{}`.

- [ ] **Step 4: ModelsSettings**

In the connection row (`Connections`), under the address, add:

```tsx
{c.pause && <PauseBadge pause={c.pause} id={c.id} />}
```

```tsx
function PauseBadge({ pause, id }: { pause: AiPause; id: number }) {
  const resume = useResumeConnection();
  const [open, setOpen] = useState(false);
  const head = pause.kind === "needs_you" ? "Stopped" : `Paused until ${formatWhen(pause.until)}`;
  return (
    <div className="mt-1 space-y-1">
      <p className="text-meta font-semibold text-warn">
        {head} — {pause.reason}
      </p>
      {pause.detail && (
        <>
          <Button tone="quiet" onClick={() => setOpen(!open)}>
            {open ? "Hide the server's message" : "Show the server's message"}
          </Button>
          {open && <p className="break-words text-meta text-ink-2">{pause.detail}</p>}
        </>
      )}
      <Button disabled={resume.isPending} onClick={() => resume.mutate({ id })}>
        Resume now
      </Button>
    </div>
  );
}
```

Use the warn colour class the codebase already uses. Grep `text-warn` or the `Notice tone="warn"` styles in `components/ui`, and pick the existing token; don't invent one.

In `ConnectionDialog`, add state `resetsAt` (default `existing?.resets_at ?? "00:00"`) and `resetsTz` (default `existing?.resets_tz ?? "UTC"`). Add two fields after the address:

```tsx
<Field label="Daily limit resets at" hint="When the provider's free daily limit starts again. Most reset at 00:00 UTC; Gemini at midnight Pacific time.">
  <input className={inputClass} type="time" value={resetsAt} onChange={(e) => setResetsAt(e.target.value)} />
</Field>
<Field label="Time zone">
  <input className={inputClass} list={zonesId} value={resetsTz} onChange={(e) => setResetsTz(e.target.value)} />
</Field>
<datalist id={zonesId}>
  {["UTC", "America/Los_Angeles", "America/New_York", "Europe/London", "Europe/Berlin", "Asia/Kolkata", "Asia/Tokyo"].map((z) => <option key={z} value={z} />)}
</datalist>
```

`zonesId` comes from `useId()`. Include `resets_at: resetsAt, resets_tz: resetsTz.trim() || "UTC"` in the connection body that `submit` sends, and add both to `ConnectionFields`.

In `TaskBlock`, where the Test result is shown on failure, use `t.error` and, when `t.detail` is set, append `` ` The server said: ${t.detail}` `` to the text. Change the `tested` helper to `(t: AiTest) => t.ok ? ... : [t.error ?? "It didn't work.", t.detail ? `The server said: ${t.detail}` : ""].filter(Boolean).join(" ")`.

- [ ] **Step 5: Status line and run dialog**

In `SortingStatus` (in `MailBackupStatus.tsx`), before the `if (sorting.warning)` check:

```tsx
if (sorting.paused) {
  return (
    <Notice tone="warn">
      Sorting is waiting. {pauseLine(sorting.paused)} {sorting.waiting.toLocaleString()} {sorting.waiting === 1 ? "mail" : "mails"} waiting.{" "}
      <Link to="/settings?tab=models" className="font-semibold text-accent">
        Settings → Models
      </Link>
    </Notice>
  );
}
```

In `RunRulesDialog.tsx`, inside the `deciding` block after `<Bar ... />`:

```tsx
{job?.paused && <p className="text-table text-ink-2">{pauseLine(job.paused)}</p>}
```

- [ ] **Step 6: Run the frontend checks**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: all pass. Test fixtures in other test files build `AiConnection`, `Sorting` or `JobPreview` objects. Where typecheck now fails because of the new required fields, add `resets_at: "00:00", resets_tz: "UTC", pause: null` and `paused: null` to those fixtures.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(web): show a paused model with its reason, Resume now, and the daily reset time

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Fake model limit, browser test, docs

**Files:**
- Modify: `backend/app/devtools/fake_model.py`
- Test: `backend/tests/test_fake_model.py`
- Modify: `frontend/e2e/rules.spec.ts` (a second `test(...)`)
- Modify: `docs/mail-rules.md` (§1 and §7), `docs/limitations.md` (Mail rules section), `CHANGELOG.md`

**Interfaces:**
- Produces: the fake model answers a request for the model `fake-limited` with HTTP 429, `Retry-After: 3600` and the body `{"error": {"message": "Rate limit reached for requests per day. Try again tomorrow."}}`. `GET /models` lists `fake-limited` too.

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_fake_model.py`, following how that file starts the server and calls it:

```python
def test_fake_limited_model_answers_429(fake_server_url):
    import httpx

    r = httpx.post(f"{fake_server_url}/chat/completions", json={"model": "fake-limited", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 429 and r.headers["Retry-After"] == "3600"
    assert "Rate limit reached" in r.json()["error"]["message"]
    models = httpx.get(f"{fake_server_url}/models").json()["data"]
    assert {"id": "fake-limited"} in models
```

`fake_server_url` stands for however the file already reaches the server. Use its real helper.

- [ ] **Step 2: Run it to check it fails**

Run: `cd backend && uv run pytest tests/test_fake_model.py -q`
Expected: FAIL (200 instead of 429).

- [ ] **Step 3: Add the limited model**

In `fake_model.py`, add `LIMITED_MODEL = "fake-limited"`. In `_send`, add an optional `headers: dict | None = None` argument and send each one. In `do_GET`, list `[{"id": MODEL}, {"id": LIMITED_MODEL}]`. In `do_POST`, after parsing `body`:

```python
        if body.get("model") == LIMITED_MODEL:
            self._send(
                {"error": {"message": "Rate limit reached for requests per day. Try again tomorrow."}},
                429,
                {"Retry-After": "3600"},
            )
            return
```

Run: `cd backend && uv run pytest tests/test_fake_model.py -q`. Expected: PASS.

- [ ] **Step 4: The browser test**

Append a second test to `frontend/e2e/rules.spec.ts`. It runs after the first test in the same file, on the same server, so it uses its own connection and removes it at the end:

```ts
test("a model that hits its limit pauses its connection until Resume now", async ({ page }) => {
  test.setTimeout(120_000);
  await ready(page);
  await page.goto("/settings?tab=models");
  await page.getByRole("button", { name: "Add connection" }).click();
  const dialog = page.getByRole("dialog", { name: "Add connection" });
  await dialog.getByLabel("Name").fill("Limited");
  await dialog.getByLabel("Address").fill(FAKE_MODEL_URL);
  await dialog.getByRole("button", { name: "Save connection" }).click();
  await expect(dialog).toBeHidden();

  const writing = page.getByRole("group", { name: "Writing rules" });
  await writing.getByLabel("Connection").selectOption({ label: "Limited" });
  await writing.getByLabel("Model").fill("fake-limited");
  await writing.getByRole("button", { name: "Test" }).click();
  await expect(writing.getByText(/The server said: Rate limit reached for requests per day/)).toBeVisible();

  const row = page.getByRole("listitem").filter({ hasText: "Limited" });
  await expect(row.getByText(/Paused until/)).toBeVisible();
  await row.getByRole("button", { name: "Show the server's message" }).click();
  await expect(row.getByText(/Try again tomorrow/)).toBeVisible();
  await row.getByRole("button", { name: "Resume now" }).click();
  await expect(row.getByText(/Paused until/)).toBeHidden();

  // Leave the settings as the first test expects them.
  await writing.getByLabel("Connection").selectOption({ label: "None" });
  await writing.getByRole("button", { name: "Save", exact: true }).click();
  await row.getByRole("button", { name: "Remove Limited" }).click();
  await expect(row).toBeHidden();
});
```

If `ready(page)` signs in only on first use and the first test leaves the page signed in, keep `ready(page)` anyway, since it is how every spec starts. Run it locally the way the first rules test runs (see the `rules` project in `frontend/playwright.config.ts` and CI's `E2E_FAKE_MODEL_URL`), with the fake model started using `DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 uv run python -m app.devtools.fake_model --port 11999`. Expected: both rules tests pass.

- [ ] **Step 5: Docs**

- **`docs/mail-rules.md` §1:** after the List models paragraph, add a paragraph on **Daily limit resets at** and its time zone, with the Gemini example. Add one sentence saying an address ending in `/chat/completions` is shortened to the part before it.
- **`docs/mail-rules.md` §7 ("When the model is down"):** rewrite it to describe the three kinds in plain words:
  - "the provider says its limit is used up: CleanSync stops calling it until the time the provider gave, or the daily reset time";
  - "it can't be reached: it waits 1 minute, then 5, 15, 60, and then 6 hours between tries";
  - "the settings are wrong (key refused, no such model): it stops until you change the connection or the task, or press Resume now or Test".
  
  Also cover:
  - the whole connection pauses, for every mailbox and task;
  - mails don't use up their tries;
  - fixed-only rules and remembered senders keep working;
  - the pause is saved and survives a restart;
  - where the line shows (the status line, Run rules, Settings → Models), and **Resume now**.
  
  Remove the old "after about an hour of failures" wording for model failures. Keep it only for mail copies that can't be read, if the doc mentions those.
- **`docs/limitations.md`, Mail rules section:** add "A paused connection pauses every task that uses it; there are no backup models yet." and "CleanSync doesn't count requests itself: it only reacts when the provider says the limit is used up."
- **`CHANGELOG.md`:** add `## Unreleased` at the top with:
  - **Added:** model connections pause when the provider's limit is used up, until the provider's time or the connection's daily reset time; the pause is saved and survives a restart; Resume now; the server's own message in Test and in Settings.
  - **Changed:** a 429 is no longer retried at once; a quota error no longer tries every answer format; `/chat/completions` is removed from an address; a 405 explains that the address usually ends in /v1.

- [ ] **Step 6: Full checks**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && cd ../frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/devtools/fake_model.py backend/tests/test_fake_model.py frontend/e2e/rules.spec.ts docs/mail-rules.md docs/limitations.md CHANGELOG.md
git commit -m "test(e2e): a limited model pauses until Resume now; docs for model limits

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
