import json
import stat

import pytest

from app.models.enums import AiTaskName
from app.services.ai.connections import AiSettings, ConnectionError_


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
