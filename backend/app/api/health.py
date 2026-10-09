from fastapi import APIRouter, Request

from app import __version__
from app.services.queue import stop_message

router = APIRouter()


@router.get("/api/health")
def health(request: Request) -> dict[str, str]:
    st = request.app.state
    stopped = stop_message(getattr(st, "queue", None)) is not None
    # Public: the login page shows the configured name before anyone logs in.
    return {
        "status": "ok",
        "version": __version__,
        "app_name": st.settings.app_name,
        "syncing": "stopped" if stopped else "on",
    }
