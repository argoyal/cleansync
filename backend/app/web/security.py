"""Security headers on every response (spec §9). nginx adds HSTS in front of the app.

A route may set its own Content-Security-Policy (e.g. /files); this middleware never
overwrites a header the route already set.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

APP_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "media-src 'self'",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
# FastAPI's Swagger page (behind the login) loads its script and styles from jsDelivr.
DOCS_CSP = "; ".join(
    [
        "default-src 'none'",
        "script-src https://cdn.jsdelivr.net 'unsafe-inline'",
        "style-src https://cdn.jsdelivr.net 'unsafe-inline'",
        "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.jsdelivr.net",
        "connect-src 'self'",
        "frame-ancestors 'none'",
    ]
)
COMMON_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
# Built assets have a content hash in their name, so they never change.
IMMUTABLE = "public, max-age=31536000, immutable"


def headers_for(path: str, status: int) -> dict[str, str]:
    headers = dict(COMMON_HEADERS)
    headers["content-security-policy"] = DOCS_CSP if path == "/api/docs" else APP_CSP
    if path.startswith("/assets/") and status == 200:
        headers["cache-control"] = IMMUTABLE
    return headers


class SecurityHeaders:
    """Plain ASGI middleware, so streamed responses (SSE, zip, video) pass through untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in headers_for(path, message["status"]).items():
                    if key not in headers:
                        headers[key] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
