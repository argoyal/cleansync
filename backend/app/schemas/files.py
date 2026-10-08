from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class CatalogOut(BaseModel):
    pair_id: int
    refreshed_at: UtcDatetime | None
    refreshing: bool
    error: str | None
    files: int
    folders: int
    bytes: int
    native_docs: int
