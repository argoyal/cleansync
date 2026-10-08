import os

import pytest

from app.models.enums import AttentionReason, PairStatus
from app.services.catalog.model import CatalogEntry
from tests.fake_rclone import jl

NOT_FOUND = {"exit": 3, "stderr": [jl("error: object not found", level="error")]}


@pytest.fixture
def env(auth_client, app, make_account, make_pair, stub_queue):
    acc = make_account()
    root = app.state.layout.account_dir(acc.email)
    pair = make_pair(acc, local_subpath="Drive", remote_path="", initialized=True)
    (root / "Drive").mkdir(exist_ok=True)
    return acc, root, pair


@pytest.fixture
def refreshes(app, monkeypatch):
    asked: list[int] = []
    monkeypatch.setattr(app.state.catalog, "request_refresh", asked.append)
    return asked


def _put(root, rel, data=b"x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _post(client, action, **body):
    return client.post(f"/api/files/{action}", json=body)


def _entry(path, is_dir=False):
    return CatalogEntry(path=path, size=0 if is_dir else 1, mtime=None, is_dir=is_dir)


def _moves(fake_rclone):
    return [a for a in fake_rclone.calls() if "moveto" in a]


def _stat_missing(fake_rclone):
    """Drive answers 'not found' when DriveSync checks that a target name is free."""
    fake_rclone.on("lsjson", **NOT_FOUND)


def test_new_folder_queues_a_sync(auth_client, env, stub_queue):
    acc, root, pair = env
    r = _post(auth_client, "mkdir", account_id=acc.id, path="Drive", name="Plans 2026")
    assert r.status_code == 200, r.text
    assert r.json() == {"paths": ["Drive/Plans 2026"], "syncs": {str(pair.id): "queued"}}
    assert (root / "Drive" / "Plans 2026").is_dir()
    assert stub_queue.requests == [(pair.id, "APP", "sync")]
    assert (
        _post(auth_client, "mkdir", account_id=acc.id, path="Drive", name="Plans 2026").status_code
        == 409
    )


@pytest.mark.parametrize(
    "name",
    [
        "a/b",
        "..",
        "RCLONE_TEST",
        "rclone_test",
        ".google-exports",
        ".Google-Exports",
        " lead",
        "tab\tname",
        "x" * 256,
        "",
    ],
)
def test_bad_names_are_refused(auth_client, env, name):
    acc, root, _ = env
    r = _post(auth_client, "mkdir", account_id=acc.id, path="Drive", name=name)
    assert r.status_code == 422
    assert [p.name for p in (root / "Drive").iterdir()] == []


def test_rename_file_and_folder_not_on_drive_yet_is_local_only(auth_client, env, fake_rclone):
    acc, root, _ = env
    _stat_missing(fake_rclone)  # no listing yet, and Drive says it doesn't have them
    _put(root, "Drive/a.txt", b"A")
    _put(root, "Drive/Old/x.txt")
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/a.txt", new_name="b.txt")
    assert r.json()["paths"] == ["Drive/b.txt"] and (root / "Drive/b.txt").read_bytes() == b"A"
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/Old", new_name="New")
    assert (root / "Drive/New/x.txt").exists()
    _put(root, "Drive/c.txt")
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/c.txt", new_name="b.txt")
    assert r.status_code == 409 and (root / "Drive/c.txt").exists()
    assert _moves(fake_rclone) == []


def test_rename_on_drive_moves_both_sides(
    auth_client, env, fake_rclone, save_catalog, refreshes, stub_queue
):
    acc, root, pair = env
    _put(root, "Drive/Old/x.txt")
    save_catalog(pair.id, [_entry("Old", is_dir=True), _entry("Old/x.txt")])
    _stat_missing(fake_rclone)
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/Old", new_name="New")
    assert r.status_code == 200, r.text
    assert r.json() == {"paths": ["Drive/New"], "syncs": {str(pair.id): "queued"}}
    assert (root / "Drive/New/x.txt").exists() and not (root / "Drive/Old").exists()
    [args] = _moves(fake_rclone)
    i = args.index("moveto")
    assert args[i + 1 : i + 3] == [f"{acc.remote_name}:Old", f"{acc.remote_name}:New"]
    assert "--drive-use-trash=true" in args
    # Checked that "New" is free on Drive first.
    stats = [a for a in fake_rclone.calls() if "lsjson" in a]
    assert stats and "--stat" in stats[0] and f"{acc.remote_name}:New" in stats[0]
    assert refreshes == [pair.id]
    assert stub_queue.requests == [(pair.id, "APP", "sync")]


def test_move_on_drive_uses_the_pair_remote_path(
    auth_client, env, make_pair, fake_rclone, save_catalog, refreshes
):
    acc, root, _ = env
    docs = make_pair(
        acc, name="Docs", local_subpath="Docs", remote_path="My Docs", initialized=True
    )
    _put(root, "Docs/a.txt")
    (root / "Docs/Dest").mkdir()
    save_catalog(docs.id, [_entry("a.txt"), _entry("Dest", is_dir=True)])
    _stat_missing(fake_rclone)
    r = _post(auth_client, "move", account_id=acc.id, paths=["Docs/a.txt"], to="Docs/Dest")
    assert r.status_code == 200, r.text
    [args] = _moves(fake_rclone)
    i = args.index("moveto")
    assert args[i + 1 : i + 3] == [
        f"{acc.remote_name}:My Docs/a.txt",
        f"{acc.remote_name}:My Docs/Dest/a.txt",
    ]
    assert (root / "Docs/Dest/a.txt").exists()
    assert refreshes == [docs.id]


def test_drive_move_failure_changes_nothing_locally(auth_client, env, fake_rclone, save_catalog):
    acc, root, pair = env
    _put(root, "Drive/a.txt")
    save_catalog(pair.id, [_entry("a.txt")])
    _stat_missing(fake_rclone)
    fake_rclone.on("moveto", exit=1, stderr=[jl("Failed to moveto: error 403", level="error")])
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/a.txt", new_name="b.txt")
    assert r.status_code == 502, r.text
    assert "Google Drive" in r.json()["detail"]
    assert (root / "Drive/a.txt").exists() and not (root / "Drive/b.txt").exists()


def test_drive_login_expired_marks_the_account(auth_client, app, env, fake_rclone, save_catalog):
    from app.models import Account

    acc, root, pair = env
    _put(root, "Drive/a.txt")
    save_catalog(pair.id, [_entry("a.txt")])
    _stat_missing(fake_rclone)
    fake_rclone.on("moveto", exit=1, stderr=[jl("Failed to moveto: invalid_grant", level="error")])
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/a.txt", new_name="b.txt")
    assert r.status_code == 502
    assert (root / "Drive/a.txt").exists()
    with app.state.session_factory() as db:
        assert db.get(Account, acc.id).status == "NEEDS_REAUTH"


def test_local_failure_moves_the_drive_item_back(
    auth_client, env, fake_rclone, save_catalog, monkeypatch
):
    from app.services.files import ops

    acc, root, pair = env
    _put(root, "Drive/a.txt")
    save_catalog(pair.id, [_entry("a.txt")])
    _stat_missing(fake_rclone)

    def refuse(scope, step):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ops, "apply_local", refuse)
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/a.txt", new_name="b.txt")
    assert r.status_code == 403, r.text
    there, back = _moves(fake_rclone)
    name = acc.remote_name
    assert there[there.index("moveto") + 1 :][:2] == [f"{name}:a.txt", f"{name}:b.txt"]
    assert back[back.index("moveto") + 1 :][:2] == [f"{name}:b.txt", f"{name}:a.txt"]
    assert (root / "Drive/a.txt").exists()


def test_name_taken_on_drive_is_refused(auth_client, env, fake_rclone, save_catalog):
    acc, root, pair = env
    _put(root, "Drive/a.txt")
    save_catalog(pair.id, [_entry("a.txt"), _entry("b.txt")])
    item = '{"Path":"c.txt","Name":"c.txt","Size":1,"IsDir":false}'
    fake_rclone.on("lsjson", match=[f"{acc.remote_name}:c.txt"], stdout=[item])
    _stat_missing(fake_rclone)
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/a.txt", new_name="b.txt")
    assert r.status_code == 409
    assert _moves(fake_rclone) == []
    # Not in the (maybe old) listing, but Drive says it is there now.
    save_catalog(pair.id, [_entry("a.txt")])
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/a.txt", new_name="c.txt")
    assert r.status_code == 409, r.text
    assert _moves(fake_rclone) == [] and (root / "Drive/a.txt").exists()


def test_busy_pair_is_refused_before_anything_moves(
    auth_client, env, fake_rclone, save_catalog, stub_queue
):
    acc, root, pair = env
    _put(root, "Drive/a.txt")
    save_catalog(pair.id, [_entry("a.txt")])
    (root / "Drive/Sub").mkdir()
    stub_queue.running.add(pair.id)
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/a.txt", new_name="b.txt")
    assert r.status_code == 409
    r = _post(auth_client, "move", account_id=acc.id, paths=["Drive/a.txt"], to="Drive/Sub")
    assert r.status_code == 409
    assert fake_rclone.calls() == [] and (root / "Drive/a.txt").exists()
    assert stub_queue.requests == []


def test_disconnected_account_refuses_drive_moves(auth_client, app, env, fake_rclone, save_catalog):
    from app.models import Account

    acc, root, pair = env
    _put(root, "Drive/a.txt")
    save_catalog(pair.id, [_entry("a.txt")])
    with app.state.session_factory() as db:
        db.get(Account, acc.id).status = "NEEDS_REAUTH"
        db.commit()
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/a.txt", new_name="b.txt")
    assert r.status_code == 409
    assert fake_rclone.calls() == [] and (root / "Drive/a.txt").exists()


def test_pair_holds_off_syncs_while_both_sides_move(
    auth_client, app, env, fake_rclone, save_catalog, stub_queue
):
    acc, root, pair = env
    _put(root, "Drive/a.txt")
    save_catalog(pair.id, [_entry("a.txt")])
    _stat_missing(fake_rclone)
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/a.txt", new_name="b.txt")
    assert r.status_code == 200
    assert stub_queue.held == [pair.id]


def test_pair_folders_cannot_be_renamed_moved_or_deleted(auth_client, env):
    acc, root, _ = env
    _put(root, "Other/x.txt")
    assert (
        _post(auth_client, "rename", account_id=acc.id, path="Drive", new_name="D2").status_code
        == 409
    )
    assert (
        _post(auth_client, "move", account_id=acc.id, paths=["Drive"], to="Other").status_code
        == 409
    )
    assert _post(auth_client, "delete", account_id=acc.id, paths=["Drive"]).status_code == 409
    assert _post(auth_client, "delete", account_id=acc.id, paths=[""]).status_code == 409
    assert (root / "Drive").is_dir()


def test_move_checks_everything_before_moving_anything(auth_client, env, fake_rclone):
    acc, root, _ = env
    _stat_missing(fake_rclone)
    _put(root, "Drive/a.txt")
    _put(root, "Drive/b.txt")
    _put(root, "Drive/Dest/b.txt")
    r = _post(
        auth_client,
        "move",
        account_id=acc.id,
        paths=["Drive/a.txt", "Drive/b.txt"],
        to="Drive/Dest",
    )
    assert r.status_code == 409
    assert (root / "Drive/a.txt").exists()  # nothing moved
    r = _post(auth_client, "move", account_id=acc.id, paths=["Drive/a.txt"], to="Drive/Dest")
    assert r.json()["paths"] == ["Drive/Dest/a.txt"]


def test_move_into_itself_is_refused(auth_client, env):
    acc, root, _ = env
    _put(root, "Drive/F/G/x.txt")
    r = _post(auth_client, "move", account_id=acc.id, paths=["Drive/F"], to="Drive/F/G")
    assert r.status_code == 422


def test_move_between_pairs_is_refused(auth_client, env, make_pair, stub_queue):
    acc, root, pair = env
    make_pair(acc, name="Other", local_subpath="Other", remote_path="Other", initialized=True)
    _put(root, "Drive/a.txt")
    (root / "Other").mkdir()
    r = _post(auth_client, "move", account_id=acc.id, paths=["Drive/a.txt"], to="Other")
    assert r.status_code == 422
    assert (root / "Drive/a.txt").exists() and stub_queue.requests == []


def test_move_out_of_a_pair_is_local_and_syncs_the_pair(
    auth_client, env, fake_rclone, save_catalog, stub_queue
):
    acc, root, pair = env
    _put(root, "Drive/a.txt")
    (root / "Loose").mkdir()
    save_catalog(pair.id, [_entry("a.txt")])
    r = _post(auth_client, "move", account_id=acc.id, paths=["Drive/a.txt"], to="Loose")
    assert r.status_code == 200, r.text
    assert r.json() == {"paths": ["Loose/a.txt"], "syncs": {str(pair.id): "queued"}}
    assert _moves(fake_rclone) == []


def test_delete_moves_items_into_recovery(auth_client, app, env):
    acc, root, pair = env
    _put(root, "Drive/Work/report.pdf", b"PDF")
    _put(root, "loose.txt", b"L")
    r = _post(
        auth_client, "delete", account_id=acc.id, paths=["Drive/Work/report.pdf", "loose.txt"]
    )
    assert r.status_code == 200, r.text
    kept = r.json()["paths"]
    backups = app.state.layout.backups_dir
    assert kept[0].startswith(f"{acc.email}/{pair.id}/") and kept[0].endswith("/Work/report.pdf")
    assert "/.drivesync-deleted-" in kept[0]
    assert (backups / kept[0]).read_bytes() == b"PDF"
    assert kept[1].startswith(f"{acc.email}/unsynced/") and (backups / kept[1]).read_bytes() == b"L"
    assert not (root / "Drive/Work/report.pdf").exists()
    assert r.json()["syncs"] == {str(pair.id): "queued"}


def test_uninitialized_pair_is_not_synced(auth_client, env, make_pair, stub_queue):
    acc, root, _ = env
    fresh = make_pair(acc, name="New", local_subpath="New", remote_path="New")
    (root / "New").mkdir()
    r = _post(auth_client, "mkdir", account_id=acc.id, path="New", name="x")
    assert r.json()["syncs"] == {str(fresh.id): "not_initialized"}
    assert stub_queue.requests == []


def test_actions_are_refused_while_syncing_is_disabled(auth_client, env, stub_queue):
    acc, root, _ = env
    _put(root, "Drive/a.txt")
    stub_queue.disabled_reason = "database integrity check failed"
    assert _post(auth_client, "delete", account_id=acc.id, paths=["Drive/a.txt"]).status_code == 503
    assert (
        _post(
            auth_client, "rename", account_id=acc.id, path="Drive/a.txt", new_name="b"
        ).status_code
        == 503
    )
    assert (root / "Drive/a.txt").exists()


def test_actions_need_the_csrf_header(auth_client, env):
    acc, _, _ = env
    del auth_client.headers["X-CSRF-Token"]
    assert _post(auth_client, "mkdir", account_id=acc.id, path="Drive", name="x").status_code == 403


def test_hidden_and_traversal_paths(auth_client, env):
    acc, root, _ = env
    _put(root, "Drive/RCLONE_TEST")
    assert (
        _post(auth_client, "delete", account_id=acc.id, paths=["Drive/RCLONE_TEST"]).status_code
        == 404
    )
    assert (
        _post(auth_client, "delete", account_id=acc.id, paths=["Drive/rclone_test"]).status_code
        == 404
    )
    assert _post(auth_client, "delete", account_id=acc.id, paths=["../x"]).status_code == 422
    assert (
        _post(auth_client, "move", account_id=acc.id, paths=["Drive/x"], to="../..").status_code
        == 422
    )


def test_symlinks_into_hidden_names_are_refused(auth_client, env):
    acc, root, _ = env
    _put(root, "Drive/RCLONE_TEST")
    (root / "Drive/.google-exports").mkdir()
    _put(root, "Drive/a.txt")
    os.symlink(root / "Drive/.google-exports", root / "Drive/exports-link")
    # A folder whose real path is a hidden folder is never a move target or a mkdir parent.
    r = _post(
        auth_client, "move", account_id=acc.id, paths=["Drive/a.txt"], to="Drive/exports-link"
    )
    assert r.status_code == 404
    r = _post(auth_client, "mkdir", account_id=acc.id, path="Drive/exports-link", name="x")
    assert r.status_code == 404
    assert (root / "Drive/a.txt").exists()
    assert list((root / "Drive/.google-exports").iterdir()) == []


async def test_app_triggered_sync_waits_for_a_blocked_pair(
    sync_engine, make_account, make_pair, fake_rclone
):
    pair = make_pair(
        make_account(),
        initialized=True,
        status=PairStatus.ATTENTION,
        attention_reason=AttentionReason.BLOCKED,
    )
    assert await sync_engine.run_pair(pair.id, "APP") is None
    assert fake_rclone.calls() == []
