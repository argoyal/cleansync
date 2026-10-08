from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.bootstrap import INITIAL_PASSWORD_FILE
from app.auth.deps import SESSION_COOKIE, AuthContext, current_session, get_db
from app.auth.passwords import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.auth.sessions import create_session, delete_other_sessions, delete_session
from app.models import User
from app.schemas.auth import ChangePasswordIn, LoginIn, MeOut

router = APIRouter(prefix="/api/auth")


def _me(ctx_user: User, csrf: str) -> MeOut:
    return MeOut(
        username=ctx_user.username,
        must_change_password=ctx_user.must_change_password,
        csrf_token=csrf,
    )


def _set_cookie(request: Request, response: Response, sid: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="strict",
        path="/",
    )


@router.post("/login", response_model=MeOut)
def login(
    body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)
) -> MeOut:
    limiter = request.app.state.login_limiter
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}|{body.username.lower()}"
    if not limiter.try_acquire(key):
        raise HTTPException(429, "too_many_attempts")
    user = db.scalar(select(User).where(User.username == body.username))
    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        raise HTTPException(401, "invalid_credentials")
    limiter.success(key)
    session = create_session(db, user)
    _set_cookie(request, response, session.id)
    return _me(user, session.csrf_token)


@router.get("/me", response_model=MeOut)
def me(ctx: AuthContext = Depends(current_session)) -> MeOut:
    return _me(ctx.user, ctx.session.csrf_token)


@router.post("/logout")
def logout(
    response: Response, ctx: AuthContext = Depends(current_session), db: Session = Depends(get_db)
) -> dict[str, bool]:
    delete_session(db, ctx.session.id)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/change-password", response_model=MeOut)
def change_password(
    body: ChangePasswordIn,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(current_session),
    db: Session = Depends(get_db),
) -> MeOut:
    limiter = request.app.state.login_limiter
    key = f"pwchange|{ctx.user.id}"
    if not limiter.try_acquire(key):
        raise HTTPException(429, "too_many_attempts")
    user = db.get(User, ctx.user.id)
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(401, "current_password_wrong")
    limiter.success(key)
    if len(body.new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(422, f"new password must be at least {MIN_PASSWORD_LENGTH} characters")
    if body.new_password == body.current_password:
        raise HTTPException(422, "new password must differ from the current one")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    db.commit()
    # Rotate: fresh session id and CSRF token, every other session is dropped.
    new_session = create_session(db, user)
    delete_other_sessions(db, user.id, new_session.id)
    _set_cookie(request, response, new_session.id)
    (request.app.state.layout.secrets_dir / INITIAL_PASSWORD_FILE).unlink(missing_ok=True)
    return _me(user, new_session.csrf_token)
