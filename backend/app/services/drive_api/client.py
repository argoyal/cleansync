"""The small Google Drive API client (spec §3): metadata only, never file content."""

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.services.catalog.model import valid_id
from app.services.drive_api.tokens import DriveApiError, DriveAuthError, TokenProvider

FILES_URL = "https://www.googleapis.com/drive/v3/files"
NATIVE_MIME = {
    "document": "application/vnd.google-apps.document",
    "spreadsheet": "application/vnd.google-apps.spreadsheet",
    "presentation": "application/vnd.google-apps.presentation",
}
DEFAULT_NAMES = {
    "document": "Untitled document",
    "spreadsheet": "Untitled spreadsheet",
    "presentation": "Untitled presentation",
}


@dataclass(frozen=True)
class CreatedFile:
    id: str
    name: str
    mime: str


class DriveApi:
    def __init__(
        self,
        tokens: TokenProvider,
        http_factory: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=30),
    ) -> None:
        self.tokens = tokens
        self.http_factory = http_factory

    async def create_native(
        self, remote_name: str, kind: str, name: str, parent_id: str
    ) -> CreatedFile:
        """A blank Google Doc/Sheets/Slides in the given Drive folder."""
        if kind not in NATIVE_MIME:
            raise DriveApiError("unknown kind of Google file")
        if valid_id(parent_id) is None:
            raise DriveApiError("that folder has no usable Google Drive ID")
        body = {"name": name, "mimeType": NATIVE_MIME[kind], "parents": [parent_id]}
        resp: httpx.Response | None = None
        for attempt in (1, 2):
            token = await self.tokens.access_token(remote_name)
            try:
                async with self.http_factory() as http:
                    resp = await http.post(
                        FILES_URL,
                        params={"fields": "id,name,mimeType"},
                        json=body,
                        headers={"Authorization": f"Bearer {token}"},
                    )
            except httpx.HTTPError:
                # The exception text can echo the request; keep it out of messages.
                raise DriveApiError("could not reach Google Drive") from None
            if resp.status_code == 401 and attempt == 1:
                self.tokens.forget(remote_name)  # expired early; try once with a new one
                continue
            break
        assert resp is not None
        if resp.status_code == 401:
            raise DriveAuthError("Google says the login expired or was revoked")
        if resp.status_code >= 400:
            raise DriveApiError(
                f"Google Drive refused to create the file (HTTP {resp.status_code})"
            )
        try:
            data = resp.json()
            file_id = valid_id(data.get("id"))
            shown = str(data.get("name", name))
        except (ValueError, AttributeError):
            file_id, shown = None, name
        if file_id is None:
            raise DriveApiError("Google Drive gave an unexpected answer")
        return CreatedFile(id=file_id, name=shown, mime=NATIVE_MIME[kind])
