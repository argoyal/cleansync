import json
import stat
from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import AiTaskName
from app.services.ai.connections import AiSettings, ConnectionError_
from app.services.ai.limits import DOWN, LIMITED, NEEDS_YOU


@pytest.fixture
def ai(session_factory, layout):
    seen: list[str] = []
    s = AiSettings(session_factory, layout.secrets_dir, on_secret=seen.append)
    s.seen = seen
    return s


def test_key_is_a_private_file_not_a_column(ai, layout, db):
    conn = ai.create("LiteLLM", "http://litellm:4000/v1/", api_key="sk-test-123456")
    key_file = layout.secrets_dir / "ai" / f"{conn.id}.key"
    assert key_file.read_text() == "sk-test-123456"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert conn.has_key is True
    assert conn.base_url == "http://litellm:4000/v1"  # trailing slash dropped
    assert "sk-test-123456" in ai.seen  # registered with the log redactor


def test_headers_are_secret_too(ai, layout):
    conn = ai.create("GW", "https://gw.example.com/v1", headers={"X-Team": "secret-team-77"})
    saved = json.loads((layout.secrets_dir / "ai" / f"{conn.id}.headers.json").read_text())
    assert saved == {"X-Team": "secret-team-77"}
    assert conn.header_names == ["X-Team"]
    assert "secret-team-77" in ai.seen


@pytest.mark.parametrize("url", ["ftp://x", "localhost:11434", "", "http://"])
def test_bad_urls_are_refused(ai, url):
    with pytest.raises(ConnectionError_):
        ai.create("x", url)


def test_update_keeps_key_unless_given_and_forgets_modes_on_new_url(ai, layout):
    conn = ai.create("A", "http://a:1/v1", api_key="sk-aaaaaaaa")
    ai.remember_mode(conn.id, "m", "json_object")
    ai.update(conn.id, name="B")
    assert (layout.secrets_dir / "ai" / f"{conn.id}.key").read_text() == "sk-aaaaaaaa"
    assert ai.remembered_mode(conn.id, "m") == "json_object"
    ai.update(conn.id, base_url="http://b:1/v1", api_key=None)
    assert not (layout.secrets_dir / "ai" / f"{conn.id}.key").exists()
    assert ai.remembered_mode(conn.id, "m") is None


def test_target_for_a_task_and_writing_rules_falls_back_to_sorting(ai):
    conn = ai.create("Ollama", "http://ollama:11434/v1")
    assert ai.target(AiTaskName.SORTING) is None
    ai.set_task(AiTaskName.SORTING, conn.id, "llama3.1:8b", temperature=0.0, timeout_s=30)
    t = ai.target(AiTaskName.WRITING_RULES)
    assert t is not None and t.model == "llama3.1:8b" and t.timeout_s == 30
    assert t.api_key is None and t.headers == {}


def test_deleting_a_connection_removes_files_and_unsets_tasks(ai, layout):
    conn = ai.create("A", "http://a:1/v1", api_key="sk-aaaaaaaa")
    ai.set_task(AiTaskName.SORTING, conn.id, "m")
    ai.delete(conn.id)
    assert not (layout.secrets_dir / "ai" / f"{conn.id}.key").exists()
    assert ai.target(AiTaskName.SORTING) is None


def test_load_secrets_registers_saved_values(session_factory, layout):
    first = AiSettings(session_factory, layout.secrets_dir)
    first.create("A", "http://a:1/v1", api_key="sk-bbbbbbbb", headers={"X-K": "hhhhhhhh"})
    seen: list[str] = []
    AiSettings(session_factory, layout.secrets_dir, on_secret=seen.append).load_secrets()
    assert set(seen) == {"sk-bbbbbbbb", "hhhhhhhh"}


@pytest.mark.parametrize(
    "url", ["http://me:pw@gw.example.com/v1", "https://token@gw.example.com/v1", "http://@a:1/v1"]
)
def test_a_user_name_or_password_in_the_address_is_refused(ai, url):
    with pytest.raises(ConnectionError_, match="Use the API key field"):
        ai.create("x", url)


def test_key_and_header_values_are_saved_without_stray_spaces(ai, layout):
    conn = ai.create("A", "http://a:1/v1", api_key="sk-cccccccc\n", headers={"X-K": " hhhh\n"})
    folder = layout.secrets_dir / "ai"
    assert (folder / f"{conn.id}.key").read_text() == "sk-cccccccc"
    assert json.loads((folder / f"{conn.id}.headers.json").read_text()) == {"X-K": "hhhh"}
    assert "sk-cccccccc" in ai.seen and "hhhh" in ai.seen
    assert ai.target_for(conn.id, "m").api_key == "sk-cccccccc"


def test_a_new_server_drops_the_saved_key_and_headers(ai, layout):
    folder = layout.secrets_dir / "ai"
    conn = ai.create("A", "http://a:1/v1", api_key="sk-aaaaaaaa", headers={"X-K": "hhhhhhhh"})
    ai.update(conn.id, base_url="http://a:1/v2")  # same server, another path: kept
    assert (folder / f"{conn.id}.key").exists() and (folder / f"{conn.id}.headers.json").exists()
    c = ai.update(conn.id, base_url="http://a:2/v2")  # another port: another server
    assert (c.has_key, c.header_names) == (False, [])
    assert not (folder / f"{conn.id}.key").exists()
    assert not (folder / f"{conn.id}.headers.json").exists()


def test_a_new_server_with_a_new_key_keeps_that_key(ai, layout):
    conn = ai.create("A", "http://a:1/v1", api_key="sk-aaaaaaaa", headers={"X-K": "hhhhhhhh"})
    c = ai.update(conn.id, base_url="https://b.example.com/v1", api_key="sk-dddddddd")
    assert c.has_key is True and c.header_names == []
    assert ai.target_for(conn.id, "m").api_key == "sk-dddddddd"


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
    ai.set_pause(
        c.id,
        LIMITED,
        T0 + timedelta(hours=14),
        "The model's usage limit is used up.",
        "Rate limit reached",
        T0,
    )
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
    ai.set_pause(
        c.id, LIMITED, datetime.now(UTC) + timedelta(hours=1), "r", None, datetime.now(UTC)
    )
    cid, name, p = ai.pause_for_task(AiTaskName.WRITING_RULES)
    assert (cid, name, p.kind) == (c.id, "Groq", LIMITED)


def test_secrets_of(ai):
    c = ai.create(
        "GW", "https://gw.example.com/v1", api_key="sk-gw-123456789", headers={"X-T": "hv-secret-1"}
    )
    assert sorted(ai.secrets_of(c.id)) == ["hv-secret-1", "sk-gw-123456789"]
