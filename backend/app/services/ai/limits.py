# backend/app/services/ai/limits.py
"""Model limits (model limits spec §4-5): what kind of failure a reply is, and how long the
connection pauses for it. Pure functions; the pause itself is saved by AiSettings."""

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.redact import redact

LIMITED, DOWN, NEEDS_YOU = "limited", "down", "needs_you"
LIMIT_WORDS = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "quota",
    "limit exceeded",
    "limit reached",
    "too many requests",
    "credits",
    "exhausted",
)
DOWN_STEPS = tuple(timedelta(minutes=m) for m in (1, 5, 15, 60, 360))
MIN_WAIT = timedelta(seconds=5)
MAX_WAIT = timedelta(days=7)
SHORT_WAIT = timedelta(minutes=2)
RESET_SOON = timedelta(minutes=1)
DETAIL_CHARS = 500
REMOVED = "[removed]"
_RESET_HEADERS = ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens", "x-ratelimit-reset")
_DURATION = re.compile(
    r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?"
    r"(?:(\d+(?:\.\d+)?)ms)?$"
)
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
    if status == 408 or status >= 500:  # a request timeout is the server's trouble, not yours
        return DOWN
    return LIMITED if has_limit_words(text) else NEEDS_YOU


def parse_duration(value: str) -> float | None:
    v = (value or "").strip().lower()
    if not v:
        return None
    try:
        num = float(v)
    except ValueError:
        pass
    else:
        return num if math.isfinite(num) else None  # "inf" and "nan" are not waits
    m = _DURATION.match(v)
    if not m or not any(m.groups()):
        return None
    h, mins, s, ms = (float(g) if g else 0.0 for g in m.groups())
    return h * 3600 + mins * 60 + s + ms / 1000


def _one_wait(name: str, value: str, now: datetime) -> datetime | None:
    # A header is untrusted input. A huge or odd value must not raise here, because this runs
    # while a failed model reply is being handled and the pause still has to be saved.
    try:
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
    except (OverflowError, ValueError, OSError):
        return None


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
        raise ValueError(
            f"{resets_tz!r} isn't a time zone CleanSync knows, such as UTC or America/Los_Angeles."
        ) from None


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
