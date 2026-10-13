from pydantic import BaseModel, Field, field_validator

from app.schemas.common import UtcDatetime

MAX_HEADERS = 20


def _check_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    # Fixed messages only: a header value is a secret and must never be echoed back.
    if headers is None:
        return None
    if len(headers) > MAX_HEADERS:
        raise ValueError(f"Use at most {MAX_HEADERS} extra headers.")
    if any(len(k) > 100 for k in headers):
        raise ValueError("A header name is too long.")
    if any(len(v) > 4000 for v in headers.values()):
        raise ValueError("A header value is too long.")
    return headers


class ConnectionIn(BaseModel):
    name: str = Field("", max_length=80)
    base_url: str = Field(max_length=500)
    api_key: str | None = Field(None, max_length=4000)
    headers: dict[str, str] = Field(default_factory=dict)
    resets_at: str = Field("00:00", max_length=5)
    resets_tz: str = Field("UTC", max_length=64)

    _headers = field_validator("headers")(_check_headers)


class ConnectionPatch(BaseModel):
    name: str | None = Field(None, max_length=80)
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=4000)
    headers: dict[str, str] | None = None
    resets_at: str | None = Field(None, max_length=5)
    resets_tz: str | None = Field(None, max_length=64)

    _headers = field_validator("headers")(_check_headers)


class PauseOut(BaseModel):
    kind: str
    until: UtcDatetime | None
    reason: str
    detail: str | None
    since: UtcDatetime
    connection_id: int
    connection_name: str


class ConnectionOut(BaseModel):
    id: int
    name: str
    base_url: str
    has_key: bool
    header_names: list[str]
    resets_at: str
    resets_tz: str
    pause: PauseOut | None = None


class TaskIn(BaseModel):
    connection_id: int | None
    model: str = Field("", max_length=200)
    temperature: float = Field(0.0, ge=0, le=2)
    timeout_s: int = Field(60, ge=5, le=600)


class TaskOut(TaskIn):
    task: str


class TestIn(TaskIn):
    __test__ = False
    connection_id: int


class TestOut(BaseModel):
    __test__ = False
    ok: bool
    took_ms: int = 0
    mode: str | None = None
    chose: str | None = None
    why: str = ""
    error: str | None = None
    detail: str | None = None
