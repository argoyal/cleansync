from pydantic import BaseModel, Field, field_validator

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

    _headers = field_validator("headers")(_check_headers)


class ConnectionPatch(BaseModel):
    name: str | None = Field(None, max_length=80)
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=4000)
    headers: dict[str, str] | None = None

    _headers = field_validator("headers")(_check_headers)


class ConnectionOut(BaseModel):
    id: int
    name: str
    base_url: str
    has_key: bool
    header_names: list[str]


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
