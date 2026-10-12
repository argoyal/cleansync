from pydantic import BaseModel, Field


class ConnectionIn(BaseModel):
    name: str = Field("", max_length=80)
    base_url: str = Field(max_length=500)
    api_key: str | None = Field(None, max_length=4000)
    headers: dict[str, str] = Field(default_factory=dict)


class ConnectionPatch(BaseModel):
    name: str | None = Field(None, max_length=80)
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=4000)
    headers: dict[str, str] | None = None


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
