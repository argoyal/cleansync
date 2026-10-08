from fastapi.testclient import TestClient

from app.main import create_app


def test_health_reports_ok_and_version(settings):
    client = TestClient(create_app(settings))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "0.1.0", "app_name": "DriveSync"}
