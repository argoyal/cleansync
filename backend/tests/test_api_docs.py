"""I9: the API docs are only for a logged-in user."""

import pytest


@pytest.mark.parametrize("path", ["/api/docs", "/api/openapi.json"])
def test_docs_need_login(client, path):
    assert client.get(path).status_code == 401


def test_docs_work_after_login(auth_client):
    r = auth_client.get("/api/openapi.json")
    assert r.status_code == 200 and r.json()["info"]["title"]
    r = auth_client.get("/api/docs")
    assert r.status_code == 200 and "/api/openapi.json" in r.text


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/api/redoc"])
def test_default_public_docs_are_off(client, path):
    assert client.get(path).status_code == 404
