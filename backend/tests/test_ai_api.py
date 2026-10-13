from datetime import timedelta

import httpx
import pytest
import respx

URL = "http://ollama.example.com:11434/v1"


@pytest.fixture(autouse=True)
def _no_pause(app):
    app.state.ai_client.retry_pause_s = 0


def make(auth_client, **extra):
    r = auth_client.post("/api/ai/connections", json={"name": "Ollama", "base_url": URL, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_connections_never_return_the_key(auth_client):
    c = make(auth_client, api_key="sk-api-secret-1", headers={"X-Team": "t-secret-22"})
    listed = auth_client.get("/api/ai/connections").text
    assert c["has_key"] is True and c["header_names"] == ["X-Team"]
    assert "sk-api-secret-1" not in listed and "t-secret-22" not in listed


def test_patch_keeps_or_removes_the_key(auth_client):
    c = make(auth_client, api_key="sk-api-secret-1")
    assert auth_client.patch(f"/api/ai/connections/{c['id']}", json={"name": "B"}).json()["has_key"]
    r = auth_client.patch(f"/api/ai/connections/{c['id']}", json={"api_key": None})
    assert r.json()["has_key"] is False


def test_bad_url_is_a_plain_400(auth_client):
    r = auth_client.post("/api/ai/connections", json={"name": "x", "base_url": "ollama:11434"})
    assert r.status_code == 400 and "http://" in r.json()["detail"]


def test_delete_connection(auth_client):
    c = make(auth_client)
    assert auth_client.delete(f"/api/ai/connections/{c['id']}").status_code == 204
    assert auth_client.delete(f"/api/ai/connections/{c['id']}").status_code == 404


def test_tasks_default_and_set(auth_client):
    assert auth_client.get("/api/ai/tasks").json()["sorting"]["model"] == ""
    c = make(auth_client)
    r = auth_client.put(
        "/api/ai/tasks/sorting", json={"connection_id": c["id"], "model": "llama3.1:8b"}
    )
    assert r.status_code == 200 and r.json()["timeout_s"] == 60
    assert (
        auth_client.put(
            "/api/ai/tasks/drafting", json={"connection_id": None, "model": ""}
        ).status_code
        == 404
    )


@respx.mock
def test_list_models_and_test_button(auth_client):
    c = make(auth_client)
    respx.get(f"{URL}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "llama3.1:8b"}]})
    )
    assert auth_client.get(f"/api/ai/connections/{c['id']}/models").json() == {
        "models": ["llama3.1:8b"]
    }
    respx.post(f"{URL}/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": '{"rule": 2, "why": "a digest"}'}}]}
        )
    )
    out = auth_client.post(
        "/api/ai/test", json={"connection_id": c["id"], "model": "llama3.1:8b"}
    ).json()
    assert out["ok"] is True and out["chose"] == "Newsletters" and out["mode"] == "json_schema"


@respx.mock
def test_test_button_reports_a_plain_error(auth_client):
    c = make(auth_client)
    respx.post(f"{URL}/chat/completions").mock(side_effect=httpx.ConnectError("no"))
    out = auth_client.post(
        "/api/ai/test", json={"connection_id": c["id"], "model": "m", "timeout_s": 5}
    ).json()
    assert out["ok"] is False and "Couldn't reach" in out["error"]


def test_needs_login(client):
    assert client.get("/api/ai/connections").status_code == 401


def test_validation_errors_never_echo_a_secret(auth_client):
    c = make(auth_client)
    long_key = "k-secret-" + "x" * 4000
    bad = [
        {"name": "x", "base_url": URL, "api_key": long_key},
        {"name": "x", "base_url": URL, "headers": {"X": ["secret-h"]}},
    ]
    for body in bad:
        r = auth_client.post("/api/ai/connections", json=body)
        assert r.status_code == 422 and "msg" in r.text
        assert "secret-h" not in r.text and "k-secret-" not in r.text
        patch = {k: v for k, v in body.items() if k in ("api_key", "headers")}
        r = auth_client.patch(f"/api/ai/connections/{c['id']}", json=patch)
        assert r.status_code == 422 and "msg" in r.text
        assert "secret-h" not in r.text and "k-secret-" not in r.text


def test_headers_are_bounded(auth_client):
    many = {f"H{i}": "v" for i in range(21)}
    for headers in (many, {"n" * 101: "v"}, {"X": "v" * 4001}):
        r = auth_client.post(
            "/api/ai/connections", json={"name": "x", "base_url": URL, "headers": headers}
        )
        assert r.status_code == 422


def test_missing_connection_is_404_and_bad_input_400(auth_client):
    assert auth_client.patch("/api/ai/connections/999", json={"name": "x"}).status_code == 404
    c = make(auth_client)
    r = auth_client.patch(f"/api/ai/connections/{c['id']}", json={"base_url": "nope"})
    assert r.status_code == 400


def test_changes_need_the_csrf_header(auth_client):
    c = make(auth_client)
    token = auth_client.headers.pop("X-CSRF-Token")
    try:
        assert auth_client.post("/api/ai/connections", json={"base_url": URL}).status_code == 403
        assert (
            auth_client.patch(f"/api/ai/connections/{c['id']}", json={"name": "z"}).status_code
            == 403
        )
        assert auth_client.delete(f"/api/ai/connections/{c['id']}").status_code == 403
        assert (
            auth_client.put("/api/ai/tasks/sorting", json={"connection_id": None}).status_code
            == 403
        )
    finally:
        auth_client.headers["X-CSRF-Token"] = token


from app.core.clock import utcnow  # noqa: E402
from app.services.ai.limits import LIMITED, NEEDS_YOU  # noqa: E402


def test_connection_shows_reset_time_and_no_pause(auth_client):
    c = make(auth_client)
    assert (c["resets_at"], c["resets_tz"], c["pause"]) == ("00:00", "UTC", None)


def test_patch_reset_time_and_bad_zone(auth_client):
    c = make(auth_client)
    r = auth_client.patch(
        f"/api/ai/connections/{c['id']}",
        json={"resets_at": "00:00", "resets_tz": "America/Los_Angeles"},
    )
    assert r.status_code == 200 and r.json()["resets_tz"] == "America/Los_Angeles"
    bad = auth_client.patch(f"/api/ai/connections/{c['id']}", json={"resets_tz": "Nowhere/Land"})
    assert bad.status_code == 400 and "time zone" in bad.json()["detail"]


def test_pause_is_listed_and_resume_clears_it(auth_client, app):
    c = make(auth_client, api_key="sk-api-secret-1")
    app.state.ai.set_pause(
        c["id"],
        LIMITED,
        utcnow() + timedelta(hours=5),
        "The model's usage limit is used up.",
        "Rate limit reached",
        utcnow(),
    )
    listed = auth_client.get("/api/ai/connections").json()[0]
    assert listed["pause"]["kind"] == LIMITED and listed["pause"]["detail"] == "Rate limit reached"
    assert listed["pause"]["connection_name"] == "Ollama"
    r = auth_client.post(f"/api/ai/connections/{c['id']}/resume")
    assert r.status_code == 200 and r.json()["pause"] is None
    assert auth_client.post("/api/ai/connections/9999/resume").status_code == 404


@respx.mock
def test_test_button_ignores_a_pause_and_clears_it_on_success(auth_client, app):
    c = make(auth_client)
    app.state.ai.set_pause(
        c["id"], NEEDS_YOU, None, "The model's server refused the API key.", None, utcnow()
    )
    respx.post(f"{URL}/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": '{"rule": 2, "why": "digest"}'}}]}
        )
    )
    r = auth_client.post("/api/ai/test", json={"connection_id": c["id"], "model": "llama3.1:8b"})
    assert r.json()["ok"] is True
    assert app.state.ai.pause_of(c["id"]) is None


@respx.mock
def test_test_button_shows_the_providers_message(auth_client):
    c = make(auth_client, api_key="sk-api-secret-1")
    respx.post(f"{URL}/chat/completions").mock(
        return_value=httpx.Response(
            429, json={"error": {"message": "Daily limit hit for sk-api-secret-1"}}
        )
    )
    r = auth_client.post(
        "/api/ai/test", json={"connection_id": c["id"], "model": "llama3.1:8b"}
    ).json()
    assert r["ok"] is False and r["detail"].startswith("Daily limit hit")
    assert "sk-api-secret-1" not in str(r)


def test_rejected_reset_time_changes_nothing(auth_client):
    c = make(auth_client)
    bad = auth_client.patch(
        f"/api/ai/connections/{c['id']}", json={"name": "Renamed", "resets_tz": "Nowhere/Land"}
    )
    assert bad.status_code == 400
    assert auth_client.get("/api/ai/connections").json()[0]["name"] == "Ollama"
    odd = auth_client.patch(f"/api/ai/connections/{c['id']}", json={"resets_at": "25:99"})
    assert odd.status_code == 400
