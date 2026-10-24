import hmac
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.sessions import load_session
from app.core.clock import utcnow
from app.models import User, UserSession
from app.services.app_settings import get_setting

SESSION_COOKIE = "ds_session"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as s:
        yield s


@dataclass
class AuthContext:
    user: User
    session: UserSession


def current_session(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    return _check_session(request, db)


def _check_session(request: Request, db: Session) -> AuthContext:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        raise HTTPException(401, "not_logged_in")
    idle = timedelta(hours=int(get_setting(db, "session_idle_hours")))  # type: ignore[arg-type]
    session = load_session(db, sid, idle, utcnow())
    if session is None:
        raise HTTPException(401, "session_expired")
    if request.method not in _SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent, session.csrf_token):
            raise HTTPException(403, "csrf_check_failed")
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, "not_logged_in")
    return AuthContext(user=user, session=session)


def require_user(ctx: AuthContext = Depends(current_session)) -> AuthContext:
    if ctx.user.must_change_password:
        raise HTTPException(403, "password_change_required")
    return ctx


def require_user_briefly(request: Request) -> None:
    """require_user for responses that stay open, like the event stream. get_db's session
    lives until the response ends, so each open tab would keep a pool connection for hours;
    this one is closed as soon as the login is checked."""
    with request.app.state.session_factory() as db:
        require_user(_check_session(request, db))
