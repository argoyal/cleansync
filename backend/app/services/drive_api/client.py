"""The small Google Drive API client (spec §3): metadata only, never file content."""

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.services.catalog.model import valid_id
from app.services.drive_api.tokens import (
    DriveApiError,
    DriveAuthError,
    DriveForbiddenError,
    DriveNotFoundError,
    TokenProvider,
    json_or_none,
    post_or_none,
)

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
            resp = await post_or_none(
                self.http_factory,
                FILES_URL,
                params={"fields": "id,name,mimeType"},
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp is not None and resp.status_code == 401 and attempt == 1:
                self.tokens.forget(remote_name)  # expired early; try once with a new one
                continue
            break
        if resp is None:
            raise DriveApiError("could not reach Google Drive")
        if resp.status_code == 401:
            raise DriveAuthError("Google says the login expired or was revoked")
        if resp.status_code == 404:
            raise DriveNotFoundError("this folder no longer exists on Google Drive")
        if resp.status_code == 403:
            raise DriveForbiddenError("no permission to add files to this Google Drive folder")
        if resp.status_code >= 400:
            raise DriveApiError(
                f"Google Drive refused to create the file (HTTP {resp.status_code})"
            )
        data = json_or_none(resp)
        file_id = valid_id(data.get("id")) if isinstance(data, dict) else None
        if file_id is None:
            raise DriveApiError("Google Drive gave an unexpected answer")
        return CreatedFile(id=file_id, name=str(data.get("name", name)), mime=NATIVE_MIME[kind])
