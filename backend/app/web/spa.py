"""Serve the built web app. A GET that matches no route and is outside /api, /files and
/assets gets index.html, so the app's own routes (/drive, /activity/3, ...) work on reload.

This hooks the 404 handler instead of adding a catch-all route, so routes added later
(by tests or future routers) are never shadowed.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

_RESERVED = {"api", "files", "assets"}


def mount_spa(app: FastAPI, dist: Path) -> None:
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    async def not_found(request: Request, exc: StarletteHTTPException) -> Response:
        path = request.url.path.lstrip("/")
        if (
            exc.status_code != 404
            or request.method not in ("GET", "HEAD")
            or path.split("/", 1)[0] in _RESERVED
        ):
            return await http_exception_handler(request, exc)
        index = dist / "index.html"
        if not index.is_file():
            exc = StarletteHTTPException(404, "the web interface is not built (run: make fe-build)")
            return await http_exception_handler(request, exc)
        if path and "/" not in path:
            top = dist / path
            if top.is_file() and top.name != "index.html":
                return FileResponse(top)  # favicon.svg and the like
        return FileResponse(index, headers={"cache-control": "no-cache"})

    app.add_exception_handler(StarletteHTTPException, not_found)
