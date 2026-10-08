from datetime import timedelta

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.deps import require_user
from app.config import Settings
from app.core.clock import utcnow
from app.main import create_app
from app.models import User, UserSession
from tests.conftest import NEW_PASSWORD


def _login(client, password):
    return client.post("/api/auth/login", json={"username": "admin", "password": password})


def test_login_sets_strict_httponly_cookie_and_returns_csrf(client, settings):
    r = _login(client, settings.admin_password)
    assert r.status_code == 200
    body = r.json()
    assert body["must_change_password"] is True
    assert len(body["csrf_token"]) > 20
    cookie = r.headers["set-cookie"]
    assert "ds_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie


def test_password_is_stored_as_argon2(app, db):
    user = db.scalar(select(User))
    assert user.password_hash.startswith("$argon2id$")


def test_lockout_after_five_failures(client, settings):
    for _ in range(5):
        assert _login(client, "wrong-password").status_code == 401
    assert _login(client, settings.admin_password).status_code == 429


def test_protected_routes_need_password_change_first(app, settings):
    router = APIRouter()

    @router.get("/api/test-protected")
    def protected(ctx=Depends(require_user)):
        return {"ok": True}

    app.include_router(router)
    with TestClient(app) as c:
        assert c.get("/api/test-protected").status_code == 401
        r = _login(c, settings.admin_password)
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        resp = c.get("/api/test-protected")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "password_change_required"
        c.post("/api/auth/change-password",
               json={"current_password": settings.admin_password, "new_password": NEW_PASSWORD})
        assert c.get("/api/test-protected").status_code == 200


def test_change_password_rules(client, settings):
    r = _login(client, settings.admin_password)
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    short = client.post("/api/auth/change-password",
                        json={"current_password": settings.admin_password, "new_password": "short"})
    assert short.status_code == 422
    wrong = client.post("/api/auth/change-password",
                        json={"current_password": "nope", "new_password": NEW_PASSWORD})
    assert wrong.status_code == 401
    same = client.post("/api/auth/change-password",
                       json={"current_password": settings.admin_password,
                             "new_password": settings.admin_password})
    assert same.status_code == 422


def test_change_password_drops_other_sessions(app, settings, db):
    with TestClient(app) as a, TestClient(app) as b:
        _login(b, settings.admin_password)
        r = _login(a, settings.admin_password)
        a.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        a.post("/api/auth/change-password",
               json={"current_password": settings.admin_password, "new_password": NEW_PASSWORD})
        assert a.get("/api/auth/me").status_code == 200
        assert b.get("/api/auth/me").status_code == 401


def test_state_change_without_csrf_header_is_rejected(client, settings):
    _login(client, settings.admin_password)
    assert client.post("/api/auth/logout").status_code == 403


def test_idle_session_expires(auth_client, db):
    s = db.scalar(select(UserSession))
    s.last_seen_at = utcnow() - timedelta(hours=13)
    db.commit()
    assert auth_client.get("/api/auth/me").status_code == 401


def test_generated_admin_password_when_env_missing(tmp_path):
    s = Settings(drivesync_root=tmp_path / "r", admin_password="")
    app = create_app(s)
    pw_file = app.state.layout.secrets_dir / "initial-admin-password"
    assert pw_file.exists()
    with TestClient(app) as c:
        assert _login(c, pw_file.read_text().strip()).status_code == 200
