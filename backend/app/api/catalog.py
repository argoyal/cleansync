from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.pairs import load_pair
from app.auth.deps import AuthContext, get_db, require_user
from app.schemas.files import CatalogOut

router = APIRouter()


@router.get("/api/sync-pairs/{pair_id}/catalog", response_model=CatalogOut)
def catalog_summary(
    pair_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    load_pair(db, pair_id)
    service = request.app.state.catalog
    cat = service.load(pair_id)
    counts = cat.summary() if cat else {"files": 0, "folders": 0, "bytes": 0, "native_docs": 0}
    return CatalogOut(
        pair_id=pair_id,
        refreshed_at=cat.refreshed_at if cat else None,
        refreshing=service.is_refreshing(pair_id),
        error=service.last_error.get(pair_id),
        **counts,
    )


# async: starting the background refresh must happen on the event loop thread.
@router.post("/api/sync-pairs/{pair_id}/catalog/refresh", status_code=202)
async def refresh_catalog(
    pair_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    load_pair(db, pair_id)
    request.app.state.catalog.request_refresh(pair_id)
    return {"result": "started"}
