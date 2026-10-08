import logging
import os
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.config import Settings
from app.models import User
from app.paths import Layout

log = logging.getLogger(__name__)


def ensure_admin(db: Session, settings: Settings, layout: Layout) -> None:
    if db.scalar(select(func.count()).select_from(User)):
        return
    password = settings.admin_password
    if not password:
        password = secrets.token_urlsafe(18)
        target = layout.secrets_dir / "initial-admin-password"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(password)
        log.warning("ADMIN_PASSWORD not set; the initial admin password was written to %s", target)
    db.add(
        User(
            username=settings.admin_username,
            password_hash=hash_password(password),
            must_change_password=True,
        )
    )
    db.commit()
