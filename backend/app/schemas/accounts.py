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
    permanent_delete: bool = False  # on means: the switch is on and Google allows it
    permanent_delete_note: str | None = None
    gmail_filters: bool = False  # on means: the switch is on and Google allows it
    gmail_filters_note: str | None = None
    has_photo: bool = False


class MailSetupOut(BaseModel):
    auth_url: str | None  # None: nothing to sign in to (a pretend account); go to Mail


class AuthUrlOut(BaseModel):
    auth_url: str


class PermanentDeleteIn(BaseModel):
    on: bool


class GmailFiltersIn(BaseModel):
    on: bool
