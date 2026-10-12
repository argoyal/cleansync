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
