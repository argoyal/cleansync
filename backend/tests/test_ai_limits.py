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
    [
        "Rate limit reached for requests",
        "QUOTA exceeded",
        "rate_limit_exceeded",
        "Too Many Requests",
        "You have exhausted your credits",
        "daily limit reached",
    ],
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
    [
        ("20", 20.0),
        ("20s", 20.0),
        ("6m0s", 360.0),
        ("1h2m3.5s", 3723.5),
        ("250ms", 0.25),
        ("", None),
        ("soon", None),
    ],
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
    out = clean_detail(
        "bad key MYSECRETVALUE99 and Zq81kd93jfk20dk3k4l5m6n7b8v9c0x1z2", ["MYSECRETVALUE99"]
    )
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
