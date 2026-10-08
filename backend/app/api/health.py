from fastapi import APIRouter, Request

from app import __version__

router = APIRouter()


@router.get("/api/health")
def health(request: Request) -> dict[str, str]:
    # Public: the login page shows the configured name before anyone logs in.
    return {"status": "ok", "version": __version__, "app_name": request.app.state.settings.app_name}
