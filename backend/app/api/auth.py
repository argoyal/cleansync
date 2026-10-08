from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

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


@router.post("/login", response_model=MeOut)
def login(
    body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)
) -> MeOut:
    limiter = request.app.state.login_limiter
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}|{body.username.lower()}"
    if limiter.is_locked(key):
        raise HTTPException(429, "too_many_attempts")
    user = db.scalar(select(User).where(User.username == body.username))
    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        limiter.record_failure(key)
        raise HTTPException(401, "invalid_credentials")
    limiter.reset(key)
    session = create_session(db, user)
    response.set_cookie(
        SESSION_COOKIE,
        session.id,
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="strict",
        path="/",
    )
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
    ctx: AuthContext = Depends(current_session),
    db: Session = Depends(get_db),
) -> MeOut:
    user = db.get(User, ctx.user.id)
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(401, "current_password_wrong")
    if len(body.new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(422, f"new password must be at least {MIN_PASSWORD_LENGTH} characters")
    if body.new_password == body.current_password:
        raise HTTPException(422, "new password must differ from the current one")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    db.commit()
    delete_other_sessions(db, user.id, ctx.session.id)
    return _me(user, ctx.session.csrf_token)
