"""API docs, only for a logged-in user (FastAPI's public /docs and /openapi.json are off)."""

from fastapi import APIRouter, Depends, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from app.auth.deps import require_user

router = APIRouter(dependencies=[Depends(require_user)], include_in_schema=False)


@router.get("/api/openapi.json")
def openapi(request: Request) -> JSONResponse:
    return JSONResponse(request.app.openapi())


@router.get("/api/docs")
def docs(request: Request) -> HTMLResponse:
    return get_swagger_ui_html(openapi_url="/api/openapi.json", title=f"{request.app.title} API")
