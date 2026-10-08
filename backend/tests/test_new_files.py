import json
import logging
import zipfile

import httpx
import pytest
import respx

from app.models import Account
from app.models.enums import AccountStatus
from app.services.accounts.oauth import TOKEN_URL
from app.services.catalog.model import CatalogEntry
from app.services.drive_api.client import FILES_URL

REFRESH = "1//refresh-token-abcdefghijk"
ACCESS = "ya29.access-token-xyz"


@pytest.fixture
def env(auth_client, app, make_account, make_pair, stub_queue, save_catalog):
    acc = make_account()
    root = app.state.layout.account_dir(acc.email)
    pair = make_pair(acc, initialized=True)
    (root / "Work").mkdir()
    save_catalog(pair.id, [CatalogEntry("Work", True, 0, None, id="WORK1")], root_id="root")
    return acc, root, pair


def _new(client, acc, kind, path="Work", name=None):
    body = {"account_id": acc.id, "path": path, "kind": kind}
    if name is not None:
        body["name"] = name
    return client.post("/api/files/new", json=body)


@pytest.mark.parametrize("kind", ["docx", "xlsx", "pptx"])
def test_new_office_file_is_a_real_blank_document(auth_client, env, kind, stub_queue):
    acc, root, pair = env
    r = _new(auth_client, acc, kind)
    assert r.status_code == 200, r.text
    rel = r.json()["path"]
    assert rel.startswith("Work/Untitled ") and rel.endswith(f".{kind}")
    with zipfile.ZipFile(root / rel) as zf:  # Office files are zip packages
        assert "[Content_Types].xml" in zf.namelist()
    assert r.json()["syncs"] == {str(pair.id): "queued"}


def test_new_office_file_names(auth_client, env):
    acc, root, _ = env
    assert _new(auth_client, acc, "docx").json()["path"] == "Work/Untitled document.docx"
    assert _new(auth_client, acc, "docx").json()["path"] == "Work/Untitled document (1).docx"
    assert _new(auth_client, acc, "xlsx", name="Budget").json()["path"] == "Work/Budget.xlsx"
    assert _new(auth_client, acc, "xlsx", name="../x").status_code == 422


def test_missing_templates_are_reported(auth_client, app, env, tmp_path):
    acc, _, _ = env
    app.state.settings.office_templates_dir = tmp_path / "none"
    assert _new(auth_client, acc, "docx").status_code == 500


def _dump(fake_rclone, acc):
    token = json.dumps(
        {"access_token": "old", "refresh_token": REFRESH, "expiry": "2026-01-01T00:00:00Z"}
    )
    fake_rclone.on(
        "config",
        match=["dump"],
        stdout=[json.dumps({acc.remote_name: {"type": "drive", "token": token}})],
    )


@respx.mock
def test_new_google_doc_is_made_on_drive_and_listed(auth_client, app, env, fake_rclone, caplog):
    caplog.set_level(logging.DEBUG)
    acc, _, pair = env
    _dump(fake_rclone, acc)
    token_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": ACCESS, "expires_in": 3599})
    )
    create = respx.post(FILES_URL).mock(
        return_value=httpx.Response(
            200, json={"id": "NEWDOC1", "name": "Untitled document", "mimeType": "x"}
        )
    )
    r = _new(auth_client, acc, "document")
    assert r.status_code == 200, r.text
    assert r.json()["google_url"] == "https://docs.google.com/document/d/NEWDOC1/edit"
    sent = json.loads(create.calls[0].request.content)
    assert sent == {
        "name": "Untitled document",
        "mimeType": "application/vnd.google-apps.document",
        "parents": ["WORK1"],
    }
    assert create.calls[0].request.headers["authorization"] == f"Bearer {ACCESS}"
    assert (
        f"refresh_token={REFRESH.replace('/', '%2F')}"
        in token_route.calls[0].request.content.decode()
    )
    assert app.state.catalog.load(pair.id).natives["Work/Untitled document.docx"].id == "NEWDOC1"
    # The access token is reused, and neither token ever reaches a log.
    assert _new(auth_client, acc, "spreadsheet").status_code == 200
    assert token_route.call_count == 1
    assert sum(1 for c in fake_rclone.calls() if c[4:6] == ["config", "dump"]) == 1
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert REFRESH not in logged and ACCESS not in logged


@respx.mock
def test_revoked_login_flags_the_account(auth_client, env, fake_rclone, db):
    acc, _, _ = env
    _dump(fake_rclone, acc)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    r = _new(auth_client, acc, "presentation")
    assert r.status_code == 409 and "reconnected" in r.json()["detail"]
    db.expire_all()
    assert db.get(Account, acc.id).status == AccountStatus.NEEDS_REAUTH


@respx.mock
def test_drive_error_is_a_502(auth_client, env, fake_rclone):
    acc, _, _ = env
    _dump(fake_rclone, acc)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": ACCESS}))
    respx.post(FILES_URL).mock(return_value=httpx.Response(403, json={"error": {}}))
    assert _new(auth_client, acc, "document").status_code == 502


def test_google_files_need_a_folder_that_is_on_drive(auth_client, env):
    acc, root, _ = env
    (root / "Work" / "New").mkdir()
    r = _new(auth_client, acc, "document", path="Work/New")
    assert r.status_code == 409 and "isn't on Google Drive yet" in r.json()["detail"]


def test_google_files_only_inside_a_pair(auth_client, app, make_account, stub_queue):
    acc = make_account("bob@example.com")
    (app.state.layout.account_dir(acc.email) / "Loose").mkdir()
    r = _new(auth_client, acc, "document", path="Loose")
    assert r.status_code == 409


def test_incomplete_saved_login_is_explained(auth_client, env, fake_rclone):
    acc, _, _ = env
    fake_rclone.on(
        "config", match=["dump"], stdout=[json.dumps({acc.remote_name: {"type": "drive"}})]
    )
    r = _new(auth_client, acc, "document")
    assert r.status_code == 502 and "reconnect" in r.json()["detail"]


def test_new_google_file_rejects_a_bad_name(auth_client, env):
    acc, _, _ = env
    assert _new(auth_client, acc, "document", name="a:b").status_code == 422


def test_google_files_not_in_hidden_or_missing_folders(auth_client, env):
    acc, _, _ = env
    assert _new(auth_client, acc, "document", path=".google-exports").status_code == 404
    assert _new(auth_client, acc, "document", path="Nope").status_code == 404


@respx.mock
def test_bad_drive_ids_from_google_are_refused(auth_client, env, fake_rclone):
    acc, _, _ = env
    _dump(fake_rclone, acc)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": ACCESS}))
    respx.post(FILES_URL).mock(return_value=httpx.Response(200, json={"id": "../bad", "name": "x"}))
    assert _new(auth_client, acc, "document").status_code == 502


def test_error_responses_never_contain_secrets(auth_client, env, fake_rclone):
    acc, _, _ = env
    fake_rclone.on("config", match=["dump"], stdout=["{not json " + REFRESH])
    r = _new(auth_client, acc, "document")
    assert r.status_code == 502
    assert REFRESH not in r.text


@pytest.mark.parametrize("kind", ["docx", "document"])
def test_new_files_refused_when_syncing_is_off(auth_client, env, stub_queue, kind):
    acc, root, _ = env
    stub_queue.disabled_reason = "database integrity check failed"
    assert _new(auth_client, acc, kind).status_code == 503
    assert not list((root / "Work").iterdir())


def test_new_office_file_is_its_own_copy(auth_client, env, app):
    acc, root, _ = env
    rel = _new(auth_client, acc, "docx").json()["path"]
    tpl = app.state.settings.office_templates_dir / "blank.docx"
    assert (root / rel).stat().st_ino != tpl.stat().st_ino
    assert not [p for p in (root / "Work").iterdir() if p.name.startswith(".drivesync-upload")]
