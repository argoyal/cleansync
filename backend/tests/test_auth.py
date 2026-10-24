from datetime import timedelta

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.deps import require_user
from app.config import Settings
from app.core.clock import as_utc, utcnow
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
        c.post(
            "/api/auth/change-password",
            json={"current_password": settings.admin_password, "new_password": NEW_PASSWORD},
        )
        assert c.get("/api/test-protected").status_code == 200


def test_change_password_rules(client, settings):
    r = _login(client, settings.admin_password)
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    short = client.post(
        "/api/auth/change-password",
        json={"current_password": settings.admin_password, "new_password": "short"},
    )
    assert short.status_code == 422
    wrong = client.post(
        "/api/auth/change-password", json={"current_password": "nope", "new_password": NEW_PASSWORD}
    )
    assert wrong.status_code == 401
    same = client.post(
        "/api/auth/change-password",
        json={"current_password": settings.admin_password, "new_password": settings.admin_password},
    )
    assert same.status_code == 422


def test_change_password_drops_other_sessions(app, settings, db):
    with TestClient(app) as a, TestClient(app) as b:
        _login(b, settings.admin_password)
        r = _login(a, settings.admin_password)
        a.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        a.post(
            "/api/auth/change-password",
            json={"current_password": settings.admin_password, "new_password": NEW_PASSWORD},
        )
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


def test_unknown_username_also_locks_out(client):
    for _ in range(5):
        r = client.post("/api/auth/login", json={"username": "ghost", "password": "x" * 12})
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"username": "ghost", "password": "x" * 12})
    assert r.status_code == 429


def test_oversized_inputs_rejected(client, settings):
    big = "x" * 1025
    assert (
        client.post("/api/auth/login", json={"username": "a" * 65, "password": "p"}).status_code
        == 422
    )
    assert (
        client.post("/api/auth/login", json={"username": "admin", "password": big}).status_code
        == 422
    )
    r = _login(client, settings.admin_password)
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": settings.admin_password, "new_password": big},
    )
    assert r.status_code == 422
    r = client.post(
        "/api/auth/change-password", json={"current_password": big, "new_password": NEW_PASSWORD}
    )
    assert r.status_code == 422


def test_wrong_csrf_token_is_rejected(client, settings):
    _login(client, settings.admin_password)
    client.headers["X-CSRF-Token"] = "not-the-token"
    assert client.post("/api/auth/logout").status_code == 403


def test_change_password_wrong_current_is_rate_limited(client, settings):
    r = _login(client, settings.admin_password)
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    body = {"current_password": "nope", "new_password": NEW_PASSWORD}
    for _ in range(5):
        assert client.post("/api/auth/change-password", json=body).status_code == 401
    good = {"current_password": settings.admin_password, "new_password": NEW_PASSWORD}
    assert client.post("/api/auth/change-password", json=good).status_code == 429


def test_change_password_rotates_session(client, settings, db):
    r = _login(client, settings.admin_password)
    old_sid = client.cookies.get("ds_session")
    old_csrf = r.json()["csrf_token"]
    client.headers["X-CSRF-Token"] = old_csrf
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": settings.admin_password, "new_password": NEW_PASSWORD},
    )
    assert r.status_code == 200
    assert "ds_session=" in r.headers["set-cookie"]
    assert "HttpOnly" in r.headers["set-cookie"]
    assert r.json()["csrf_token"] != old_csrf
    new_sid = client.cookies.get("ds_session")
    assert new_sid != old_sid
    assert [s.id for s in db.scalars(select(UserSession))] == [new_sid]
    with TestClient(client.app) as other:
        other.cookies.set("ds_session", old_sid)
        assert other.get("/api/auth/me").status_code == 401


def test_initial_password_file_removed_after_change(tmp_path):
    s = Settings(drivesync_root=tmp_path / "r", admin_password="")
    app = create_app(s)
    pw_file = app.state.layout.secrets_dir / "initial-admin-password"
    initial = pw_file.read_text().strip()
    with TestClient(app) as c:
        r = _login(c, initial)
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        r = c.post(
            "/api/auth/change-password",
            json={"current_password": initial, "new_password": NEW_PASSWORD},
        )
        assert r.status_code == 200
    assert not pw_file.exists()


def test_last_seen_is_saved_at_most_once_a_minute(auth_client, app):
    """Every request checks the login. Saving the time on each one would make every page a
    write, queued behind any background job holding the database."""
    with app.state.session_factory() as db:
        s = db.scalar(select(UserSession))
        s.last_seen_at = utcnow() - timedelta(seconds=20)
        db.commit()
        before = as_utc(s.last_seen_at)
    assert auth_client.get("/api/auth/me").status_code == 200
    with app.state.session_factory() as db:
        assert as_utc(db.scalar(select(UserSession)).last_seen_at) == before
        s = db.scalar(select(UserSession))
        s.last_seen_at = utcnow() - timedelta(minutes=2)
        db.commit()
    assert auth_client.get("/api/auth/me").status_code == 200
    with app.state.session_factory() as db:
        seen = as_utc(db.scalar(select(UserSession)).last_seen_at)
        assert utcnow() - seen < timedelta(seconds=5)
