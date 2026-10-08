from fastapi import FastAPI

from app.api import health
from app.config import Settings
from app.paths import Layout


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    layout = Layout(settings.drivesync_root)
    layout.ensure()
    app = FastAPI(title=settings.app_name, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.layout = layout
    app.include_router(health.router)
    return app
