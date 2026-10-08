from datetime import datetime

from pydantic import BaseModel


class AccountOut(BaseModel):
    id: int
    email: str
    status: str
    quota_total: int | None
    quota_used: int | None
    quota_trash: int | None
    connected_at: datetime
    last_checked_at: datetime | None


class AuthUrlOut(BaseModel):
    auth_url: str
