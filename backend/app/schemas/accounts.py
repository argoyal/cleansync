from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class AccountOut(BaseModel):
    id: int
    email: str
    status: str
    provider: str
    quota_total: int | None
    quota_used: int | None
    quota_trash: int | None
    connected_at: UtcDatetime
    last_checked_at: UtcDatetime | None
    services: dict[str, str] = {}
    mail_permission: bool = False


class MailSetupOut(BaseModel):
    auth_url: str | None  # None: nothing to sign in to (a pretend account); go to Mail


class AuthUrlOut(BaseModel):
    auth_url: str
