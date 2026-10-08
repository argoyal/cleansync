from fastapi.testclient import TestClient

from app.main import create_app


def test_health_reports_ok_and_version():
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "0.1.0"}
