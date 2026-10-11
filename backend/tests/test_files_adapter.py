import pytest

from app.models import Account
from app.services.adapters.files import FilesAdapters, GoogleDriveFiles, UnknownProvider
from app.services.drive_api.client import CreatedFile, DriveFileState


class Api:
    def __init__(self):
        self.calls = []

    async def create_native(self, remote, kind, name, parent):
        self.calls.append(("create", remote, kind, name, parent))
        return CreatedFile(id="F1", name=name, mime="application/vnd.google-apps.document")

    async def file_state(self, remote, fid):
        self.calls.append(("state", remote, fid))
        return DriveFileState(trashed=True, size=1, md5="a")

    async def trashed_state(self, remote, fid):
        return True

    async def delete_forever(self, remote, fid, *, size, md5):
        self.calls.append(("delete", remote, fid, size, md5))
        return True


async def test_google_drive_files_hands_every_call_to_the_drive_api_of_the_moment():
    holder = {"api": Api(), "mover": object()}
    files = GoogleDriveFiles(lambda: holder["api"], lambda: holder["mover"])
    assert (await files.create_native("gd_a", "document", "Notes", "P1")).id == "F1"
    holder["api"] = second = Api()  # tests (and only tests) swap the object later
    assert (await files.file_state("gd_a", "F1")).trashed is True
    assert await files.delete_forever("gd_a", "F1", size=1, md5="a") is True
    assert second.calls == [("state", "gd_a", "F1"), ("delete", "gd_a", "F1", 1, "a")]
    assert files.mover is holder["mover"]
    assert files.provider == "google"


def test_adapters_pick_by_provider_and_refuse_an_unknown_one():
    google = GoogleDriveFiles(Api, object)
    adapters = FilesAdapters({"google": google})
    assert (
        adapters.for_account(Account(email="a@example.com", remote_name="gd_a", provider="google"))
        is google
    )
    assert adapters.google is google
    with pytest.raises(UnknownProvider, match="No file storage for microsoft accounts yet"):
        adapters.for_account(
            Account(email="b@example.com", remote_name="od_b", provider="microsoft")
        )


def test_the_app_has_one_files_adapter_for_google(app):
    assert app.state.files.google.mover is app.state.drive_mover
