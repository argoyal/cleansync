import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.clock import as_utc, utcnow
from app.models import User, UserSession


def create_session(db: Session, user: User) -> UserSession:
    s = UserSession(
        id=secrets.token_urlsafe(32),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        last_seen_at=utcnow(),
    )
    db.add(s)
    db.commit()
    return s


# The idle limit is hours long, so the time a login was last used only needs saving now and
# then. Saving it on every request made every page a write, queued behind background jobs.
SEEN_EVERY = timedelta(minutes=1)


def load_session(db: Session, sid: str, idle: timedelta, now: datetime) -> UserSession | None:
    s = db.get(UserSession, sid)
    if s is None:
        return None
    if now - as_utc(s.last_seen_at) > idle:
        db.delete(s)
        db.commit()
        return None
    if now - as_utc(s.last_seen_at) >= SEEN_EVERY:
        s.last_seen_at = now
        db.commit()
    return s


def delete_session(db: Session, sid: str) -> None:
    db.execute(delete(UserSession).where(UserSession.id == sid))
    db.commit()


def delete_other_sessions(db: Session, user_id: int, keep_sid: str) -> None:
    db.execute(
        delete(UserSession).where(UserSession.user_id == user_id, UserSession.id != keep_sid)
    )
    db.commit()
