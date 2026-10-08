from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class AccountOut(BaseModel):
    id: int
    email: str
    status: str
    quota_total: int | None
    quota_used: int | None
    quota_trash: int | None
    connected_at: UtcDatetime
    last_checked_at: UtcDatetime | None


class AuthUrlOut(BaseModel):
    auth_url: str
