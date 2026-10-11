"""Files: how CleanSync reaches a provider's file storage (spec §3.2).

Syncing itself is rclone (bisync) for every provider; this adapter covers the calls CleanSync
makes outside a sync run: new Google files, server-side moves, trash checks and deleting
from the trash. Only Google Drive exists today; OneDrive later brings its own.
"""

from collections.abc import Callable
from typing import Protocol

from app.models import Account
from app.models.enums import Provider
from app.services.drive_api.client import CreatedFile, DriveApi, DriveFileState
from app.services.files.moves import DriveMover

NO_FILES_MSG = "No file storage for {provider} accounts yet."


class UnknownProvider(LookupError):
    pass


class FilesAdapter(Protocol):
    provider: str

    @property
    def mover(self) -> DriveMover: ...

    async def create_native(
        self, remote_name: str, kind: str, name: str, parent_id: str
    ) -> CreatedFile: ...

    async def file_state(self, remote_name: str, file_id: str) -> DriveFileState | None: ...

    async def trashed_state(self, remote_name: str, file_id: str) -> bool | None: ...

    async def delete_forever(
        self, remote_name: str, file_id: str, *, size: int, md5: str
    ) -> bool: ...


class GoogleDriveFiles:
    """Today's Drive code, unchanged: DriveApi for metadata and the trash, DriveMover (rclone)
    for server-side moves. Both are looked up on every call."""

    provider = Provider.GOOGLE

    def __init__(
        self, get_api: Callable[[], DriveApi], get_mover: Callable[[], DriveMover]
    ) -> None:
        self._get_api = get_api
        self._get_mover = get_mover

    @property
    def mover(self) -> DriveMover:
        return self._get_mover()

    async def create_native(
        self, remote_name: str, kind: str, name: str, parent_id: str
    ) -> CreatedFile:
        return await self._get_api().create_native(remote_name, kind, name, parent_id)

    async def file_state(self, remote_name: str, file_id: str) -> DriveFileState | None:
        return await self._get_api().file_state(remote_name, file_id)

    async def trashed_state(self, remote_name: str, file_id: str) -> bool | None:
        return await self._get_api().trashed_state(remote_name, file_id)

    async def delete_forever(self, remote_name: str, file_id: str, *, size: int, md5: str) -> bool:
        return await self._get_api().delete_forever(remote_name, file_id, size=size, md5=md5)


class FilesAdapters:
    def __init__(self, adapters: dict[str, FilesAdapter]) -> None:
        self._adapters = adapters

    @property
    def google(self) -> FilesAdapter:
        return self._adapters[Provider.GOOGLE]

    def for_account(self, account: Account) -> FilesAdapter:
        adapter = self._adapters.get(account.provider)
        if adapter is None:
            raise UnknownProvider(NO_FILES_MSG.format(provider=account.provider))
        return adapter
