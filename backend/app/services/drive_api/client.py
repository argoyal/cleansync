"""The small Google Drive API client (spec §3): metadata only, never file content."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from app.services.catalog.model import valid_id
from app.services.drive_api.tokens import (
    DriveApiError,
    DriveAuthError,
    DriveForbiddenError,
    DriveNotFoundError,
    TokenProvider,
    json_or_none,
    request_or_none,
)

if TYPE_CHECKING:
    from app.services.google.client import GoogleClient

FILES_URL = "https://www.googleapis.com/drive/v3/files"
ABOUT_URL = "https://www.googleapis.com/drive/v3/about"
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


@dataclass(frozen=True)
class DriveFileState:
    """What Drive says about one file: in the trash or not, and its size and MD5."""

    trashed: bool
    size: int | None
    md5: str | None

    def matches(self, size: int | None, md5: str | None) -> bool:
        """The same size and MD5 as the saved list. Nothing to compare is never a match."""
        if size is None or md5 is None or self.size is None or self.md5 is None:
            return False
        return self.size == size and self.md5.lower() == md5.lower()


def _file_id(file_id: str) -> str:
    if valid_id(file_id) is None:
        raise DriveApiError("not a usable Google Drive ID")
    return file_id


def _parse_state(data: object) -> DriveFileState | None:
    if not isinstance(data, dict):
        return None
    trashed = data.get("trashed")
    if not isinstance(trashed, bool):
        return None
    size: int | None = None
    raw_size = data.get("size")
    if raw_size is not None:
        if not isinstance(raw_size, str | int) or isinstance(raw_size, bool):
            return None
        text = str(raw_size)
        if not text.isdigit():
            return None
        size = int(text)
    md5 = data.get("md5Checksum")
    if md5 is not None and not isinstance(md5, str):
        return None
    return DriveFileState(trashed=trashed, size=size, md5=md5)


class DriveApi:
    def __init__(
        self,
        tokens: TokenProvider,
        http_factory: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=30),
        google: "GoogleClient | None" = None,
    ) -> None:
        self.tokens = tokens
        self.http_factory = http_factory
        self.google = google

    async def _send(self, remote_name: str, op: str, method: str, url: str, **kwargs):
        if self.google is None:
            return await request_or_none(self.http_factory, method, url, **kwargs)
        # Counted against the day's budget (spec §7) but never refused or held back by it:
        # Drive behaves as in 0.1.0.
        return await self.google.request(
            remote_name,
            op,
            method,
            url,
            enforce_budget=False,
            wait_out_rate_limits=False,
            http_factory=self.http_factory,
            **kwargs,
        )

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
            resp = await self._send(
                remote_name,
                "drive.files.create",
                "POST",
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

    async def _call(
        self, remote_name: str, op: str, method: str, url: str, **kwargs
    ) -> httpx.Response:
        resp: httpx.Response | None = None
        for attempt in (1, 2):
            token = await self.tokens.access_token(remote_name)
            resp = await self._send(
                remote_name,
                op,
                method,
                url,
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )
            if resp is not None and resp.status_code == 401 and attempt == 1:
                self.tokens.forget(remote_name)  # expired early; try once with a new one
                continue
            break
        if resp is None:
            raise DriveApiError("could not reach Google Drive")
        if resp.status_code == 401:
            raise DriveAuthError("Google says the login expired or was revoked")
        return resp

    async def file_state(self, remote_name: str, file_id: str) -> DriveFileState | None:
        """Whether one file is in the trash, with its size and MD5. None: Google has no such
        file (deleted for good). Anything unexpected raises DriveApiError."""
        resp = await self._call(
            remote_name,
            "drive.files.get",
            "GET",
            f"{FILES_URL}/{_file_id(file_id)}",
            params={"fields": "id,trashed,size,md5Checksum", "supportsAllDrives": "true"},
        )
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise DriveApiError(f"Google Drive refused to look up a file (HTTP {resp.status_code})")
        state = _parse_state(json_or_none(resp))
        if state is None:
            raise DriveApiError("Google Drive gave an unexpected answer")
        return state

    async def photo_link(self, remote_name: str) -> str | None:
        """The account's Google profile photo address, or None on any failure."""
        try:
            resp = await self._call(
                remote_name,
                "drive.files.get",
                "GET",
                ABOUT_URL,
                params={"fields": "user(photoLink)"},
            )
            if resp.status_code != 200:
                return None
            data = json_or_none(resp)
            link = data["user"].get("photoLink") if isinstance(data, dict) else None
            return link if isinstance(link, str) else None
        except Exception:
            return None

    async def trashed_state(self, remote_name: str, file_id: str) -> bool | None:
        """True: in the trash. False: not in the trash. None: Google has no such file.

        This alone does not say the file is the one that was checked: compare file_state()
        with the saved size and MD5 for that.
        """
        state = await self.file_state(remote_name, file_id)
        return None if state is None else state.trashed

    async def delete_forever(self, remote_name: str, file_id: str, *, size: int, md5: str) -> bool:
        """Delete one file for good, only while it is in the trash and still has the saved
        size and MD5 (so it is the copy this server keeps).

        Returns True when it is gone, False when it was left alone: it is not in the trash
        (someone took it out) or it is not the saved file any more.

        Drive has no "delete only if unchanged": a file taken out of the trash or changed in
        the moment between the check and the delete is still deleted. The gap is a single
        request long; callers must not treat this as one atomic step.
        """
        state = await self.file_state(remote_name, file_id)
        if state is None:
            return True
        if not state.trashed or not state.matches(size, md5):
            return False
        resp = await self._call(
            remote_name,
            "drive.files.delete",
            "DELETE",
            f"{FILES_URL}/{file_id}",
            params={"supportsAllDrives": "true"},
        )
        if resp.status_code in (200, 204, 404):
            return True
        raise DriveApiError(
            f"Google Drive refused to delete a file from the trash (HTTP {resp.status_code})"
        )
