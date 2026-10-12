from tests.test_health import project_version


def test_system_info(auth_client):
    body = auth_client.get("/api/system").json()
    assert body["app_version"] == project_version()
    assert body["rclone_version"].startswith("rclone v")
    assert body["python_version"].startswith("3.")
    assert body["watcher"] in ("off", "active", "limited")
    assert body["db_ok"] is True
    assert body["insecure_http"] is True
    assert "secret" not in str(body).lower()


def test_settings_roundtrip_and_validation(auth_client):
    assert auth_client.get("/api/settings").json()["default_max_deletions"] == 10
    r = auth_client.put("/api/settings", json={"default_max_deletions": 25})
    assert r.json()["default_max_deletions"] == 25
    assert auth_client.put("/api/settings", json={"bogus": 1}).status_code == 422


def test_system_info_reports_fake_rclone_version(auth_client):
    assert auth_client.get("/api/system").json()["rclone_version"] == "rclone v1.99.0-fake"


def test_settings_wrong_type_is_422(auth_client):
    assert auth_client.put("/api/settings", json={"default_max_deletions": "x"}).status_code == 422
    assert auth_client.put("/api/settings", json={"log_retention_days": 10**9}).status_code == 422


def test_new_routes_require_login(client):
    for path in ("/api/status", "/api/system", "/api/settings"):
        assert client.get(path).status_code == 401
