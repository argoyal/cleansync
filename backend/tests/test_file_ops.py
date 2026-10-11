import os
from datetime import UTC, datetime

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


T = 1_700_000_000  # every test file's modified time, matching its Drive entry below


def _put(root, rel, data=b"x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.utime(p, (T, T))
    return p


def _post(client, action, **body):
    return client.post(f"/api/files/{action}", json=body)


def _entry(path, is_dir=False, id=None):
    mtime = datetime.fromtimestamp(T, UTC)
    return CatalogEntry(path=path, size=0 if is_dir else 1, mtime=mtime, is_dir=is_dir, id=id)


def _moves(fake_rclone):
    return [a for a in fake_rclone.calls() if "moveto" in a]


def _stat_missing(fake_rclone):
    """Drive answers 'not found' when CleanSync checks that a target name is free."""
    fake_rclone.on("lsjson", **NOT_FOUND)


def test_new_folder_queues_a_sync(auth_client, env, stub_queue):
    acc, root, pair = env
    r = _post(auth_client, "mkdir", account_id=acc.id, path="Drive", name="Plans 2026")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "paths": ["Drive/Plans 2026"],
        "syncs": {str(pair.id): "queued"},
        "drive": [],
    }
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
    assert r.json() == {
        "paths": ["Drive/New"],
        "syncs": {str(pair.id): "queued"},
        "drive": ["Drive/New"],
    }
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


# ---- fix round 1 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "trailing.",
        "CON",
        "con.txt",
        "Lpt9.log",
        "COM1",
        "nul.tar.gz",
        "a\\b",
        "a:b",
        "a*b",
        "a?b",
        'a"b',
        "a<b",
        "a>b",
        "a|b",
        "x\x85y",
        "evil‮txt.exe",
        "a⁦b",
    ],
)
def test_names_windows_cannot_hold_are_refused(name):
    from app.core.pathsafe import UnsafePath
    from app.services.files.names import validate_name

    with pytest.raises(UnsafePath):
        validate_name(name)


def test_names_are_nfc_normalized_and_lookalikes_are_fine(auth_client, env):
    from app.services.files.names import validate_name

    assert validate_name("CONSOLE.txt") == "CONSOLE.txt"
    assert validate_name("comma") == "comma"
    acc, root, _ = env
    r = _post(auth_client, "mkdir", account_id=acc.id, path="Drive", name="café")
    assert r.json()["paths"] == ["Drive/café"]


def test_item_missing_from_an_old_listing_is_checked_on_drive(
    auth_client, env, fake_rclone, save_catalog
):
    acc, root, pair = env
    _put(root, "Drive/new.txt")
    save_catalog(pair.id, [])  # listed before new.txt reached Drive
    fake_rclone.on("lsjson", match=[f"{acc.remote_name}:new.txt"], stdout=['{"Path":"new.txt"}'])
    _stat_missing(fake_rclone)
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/new.txt", new_name="n.txt")
    assert r.status_code == 200, r.text
    [args] = _moves(fake_rclone)
    assert f"{acc.remote_name}:new.txt" in args and f"{acc.remote_name}:n.txt" in args


def test_no_listing_and_no_login_refuses_moves_in_an_initialized_pair(
    auth_client, app, env, fake_rclone
):
    from app.models import Account

    acc, root, _ = env
    _put(root, "Drive/a.txt")
    with app.state.session_factory() as db:
        db.get(Account, acc.id).status = "NEEDS_REAUTH"
        db.commit()
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/a.txt", new_name="b.txt")
    assert r.status_code == 409
    assert fake_rclone.calls() == [] and (root / "Drive/a.txt").exists()


def test_delete_is_refused_while_the_pair_is_busy(auth_client, env, stub_queue):
    acc, root, pair = env
    _put(root, "Drive/a.txt")
    stub_queue.running.add(pair.id)
    assert _post(auth_client, "delete", account_id=acc.id, paths=["Drive/a.txt"]).status_code == 409
    assert (root / "Drive/a.txt").exists() and stub_queue.requests == []
    stub_queue.running.clear()
    assert _post(auth_client, "delete", account_id=acc.id, paths=["Drive/a.txt"]).status_code == 200
    assert stub_queue.held == [pair.id]


def test_partial_delete_says_what_was_done(auth_client, env, stub_queue, monkeypatch):
    from app.services.files import ops

    acc, root, pair = env
    for n in ("a", "b", "c"):
        _put(root, f"Drive/{n}.txt")
    real = ops.shutil.move

    def flaky(src, dst):
        if src.endswith("b.txt"):
            raise PermissionError(13, "Permission denied")
        return real(src, dst)

    monkeypatch.setattr(ops.shutil, "move", flaky)
    paths = ["Drive/a.txt", "Drive/b.txt", "Drive/c.txt"]
    r = _post(auth_client, "delete", account_id=acc.id, paths=paths)
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["done"] == ["Drive/a.txt"] and detail["failed"] == ["Drive/b.txt"]
    assert detail["not_done"] == ["Drive/c.txt"] and "not allowed" in detail["message"]
    assert not (root / "Drive/a.txt").exists() and (root / "Drive/c.txt").exists()
    assert stub_queue.requests == [(pair.id, "APP", "sync")]  # a.txt is gone: sync anyway


def test_partial_move_says_what_was_done(auth_client, env, fake_rclone, save_catalog):
    acc, root, pair = env
    for n in ("a", "b", "c"):
        _put(root, f"Drive/{n}.txt")
    (root / "Drive/Dest").mkdir()
    save_catalog(pair.id, [_entry("a.txt"), _entry("b.txt"), _entry("c.txt")])
    fake_rclone.on("moveto", match=[f"{acc.remote_name}:b.txt"], exit=1, stderr=["Error: 403"])
    _stat_missing(fake_rclone)
    paths = ["Drive/a.txt", "Drive/b.txt", "Drive/c.txt"]
    r = _post(auth_client, "move", account_id=acc.id, paths=paths, to="Drive/Dest")
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert (detail["done"], detail["failed"], detail["not_done"]) == (
        ["Drive/a.txt"],
        ["Drive/b.txt"],
        ["Drive/c.txt"],
    )
    assert (root / "Drive/Dest/a.txt").exists() and (root / "Drive/b.txt").exists()


def test_repeated_and_nested_selections_are_done_once(auth_client, app, env, fake_rclone):
    acc, root, _ = env
    _stat_missing(fake_rclone)
    _put(root, "Drive/F/x.txt")
    _put(root, "Drive/G/y.txt")
    (root / "Drive/Dest").mkdir()
    r = _post(
        auth_client,
        "move",
        account_id=acc.id,
        paths=["Drive/F", "Drive/F/x.txt", "Drive/F"],
        to="Drive/Dest",
    )
    assert r.status_code == 200, r.text
    assert r.json()["paths"] == ["Drive/Dest/F"] and (root / "Drive/Dest/F/x.txt").exists()
    r = _post(
        auth_client, "delete", account_id=acc.id, paths=["Drive/G/y.txt", "Drive/G", "Drive/G"]
    )
    assert r.status_code == 200, r.text
    [kept] = r.json()["paths"]
    assert (app.state.layout.backups_dir / kept / "y.txt").read_bytes() == b"x"


def test_each_delete_gets_a_new_recovery_folder(auth_client, app, env, monkeypatch):
    from app.services.files import ops

    acc, root, pair = env
    _put(root, "Drive/a.txt")
    tokens = iter(["aaaa", "bbbb"])
    monkeypatch.setattr(ops.secrets, "token_hex", lambda n: next(tokens))
    monkeypatch.setattr(
        "app.api.fileops.utcnow", lambda: __import__("datetime").datetime(2026, 10, 8, 12, 0, 0)
    )
    day = app.state.layout.backups_dir / acc.email / str(pair.id) / "2026-10-08"
    (day / ".drivesync-deleted-120000-aaaa").mkdir(parents=True)  # an earlier delete
    r = _post(auth_client, "delete", account_id=acc.id, paths=["Drive/a.txt"])
    assert r.status_code == 200, r.text
    assert "/.drivesync-deleted-120000-bbbb/" in r.json()["paths"][0]
    assert list((day / ".drivesync-deleted-120000-aaaa").iterdir()) == []


def test_failed_undo_says_the_next_sync_will_reconcile(
    auth_client, app, env, fake_rclone, save_catalog, monkeypatch
):
    from app.services.files import ops

    acc, root, pair = env
    _put(root, "Drive/a.txt")
    save_catalog(pair.id, [_entry("a.txt")])
    _stat_missing(fake_rclone)
    calls = []

    async def move(account, src, dst):
        calls.append((src, dst))
        if len(calls) > 1:
            raise ops.FileOpError("Google Drive refused to move the item: offline", 502)

    monkeypatch.setattr(app.state.drive_mover, "move", move)

    def refuse(scope, step):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ops, "apply_local", refuse)
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/a.txt", new_name="b.txt")
    assert r.status_code == 403
    assert "next sync will reconcile" in r.json()["detail"]
    assert len(calls) == 2


class _RecordingDrive:
    def __init__(self):
        self.moves = []

    async def move(self, account, src, dst):
        self.moves.append((src, dst))


class _Holds:
    def __init__(self):
        self.held = set()

    def hold(self, pid):
        import contextlib

        @contextlib.contextmanager
        def _h():
            self.held.add(pid)
            try:
                yield
            finally:
                self.held.discard(pid)

        return _h()


async def _cancel_mid_local_move(tmp_path, monkeypatch, *, thread_fails):
    """Cancel the request while the disk move is running in its thread, then let it end."""
    import asyncio
    import threading

    from app.services.files import moves, ops
    from app.services.files.scope import Scope

    root = tmp_path / "acct"
    (root / "Drive").mkdir(parents=True)
    (root / "Drive/a.txt").write_text("A")
    pair = type("P", (), {"id": 7, "local_subpath": "Drive"})()
    scope = Scope(account=type("A", (), {"id": 1})(), root=root, pairs=[pair])
    started, release = threading.Event(), threading.Event()
    real = ops.apply_local

    def slow(scope, step):
        started.set()
        release.wait(5)
        if thread_fails:
            raise PermissionError(13, "Permission denied")
        real(scope, step)

    monkeypatch.setattr(ops, "apply_local", slow)
    drive, queue = _RecordingDrive(), _Holds()
    step = ops.MoveStep("Drive/a.txt", "Drive/b.txt", pair)
    plan = moves.MovePlan(scope, [moves._Planned(step, "gd:a.txt", "gd:b.txt")])
    task = asyncio.ensure_future(moves.run_moves(plan, queue=queue, drive=drive))
    while not started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done() and queue.held == {7}  # still waiting for the thread, pair held
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert queue.held == set()
    return root, drive


async def test_cancelled_request_whose_disk_move_finished_keeps_drive_moved(tmp_path, monkeypatch):
    root, drive = await _cancel_mid_local_move(tmp_path, monkeypatch, thread_fails=False)
    assert drive.moves == [("gd:a.txt", "gd:b.txt")]  # not moved back
    assert (root / "Drive/b.txt").read_text() == "A" and not (root / "Drive/a.txt").exists()


async def test_cancelled_request_whose_disk_move_failed_moves_drive_back(tmp_path, monkeypatch):
    root, drive = await _cancel_mid_local_move(tmp_path, monkeypatch, thread_fails=True)
    assert drive.moves == [("gd:a.txt", "gd:b.txt"), ("gd:b.txt", "gd:a.txt")]
    assert (root / "Drive/a.txt").exists() and not (root / "Drive/b.txt").exists()


def test_no_login_and_an_item_missing_from_the_listing_is_refused(
    auth_client, app, env, fake_rclone, save_catalog
):
    from app.models import Account

    acc, root, pair = env
    _put(root, "Drive/new.txt")
    save_catalog(pair.id, [])  # may be old: CleanSync can't tell whether new.txt is on Drive
    with app.state.session_factory() as db:
        db.get(Account, acc.id).status = "NEEDS_REAUTH"
        db.commit()
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/new.txt", new_name="n.txt")
    assert r.status_code == 409
    assert fake_rclone.calls() == [] and (root / "Drive/new.txt").exists()


def test_a_pair_that_turns_busy_before_the_delete_hold_is_a_409(
    auth_client, env, stub_queue, monkeypatch
):
    import contextlib

    acc, root, pair = env
    _put(root, "Drive/a.txt")

    @contextlib.contextmanager
    def busy(pid):
        raise RuntimeError("busy")
        yield

    monkeypatch.setattr(stub_queue, "hold", busy)
    assert _post(auth_client, "delete", account_id=acc.id, paths=["Drive/a.txt"]).status_code == 409
    assert (root / "Drive/a.txt").exists()


def test_moving_a_folder_into_itself_ignores_case(auth_client, env):
    acc, root, _ = env
    (root / "Drive/F/G").mkdir(parents=True)
    (root / "Drive/f/G").mkdir(parents=True, exist_ok=True)  # the same folder on macOS
    r = _post(auth_client, "move", account_id=acc.id, paths=["Drive/F"], to="Drive/f/G")
    assert r.status_code == 422


@pytest.fixture
def drive_mover(layout, fake_rclone):
    from app.services.files.moves import DriveMover
    from app.services.rclone.commands import RcloneEnv

    class Accounts:
        async def mark_needs_reauth(self, *a):
            pass

    env = RcloneEnv(
        binary=fake_rclone.binary,
        config_file=layout.rclone_conf,
        cache_dir=layout.rclone_cache,
        config_pass="",
    )
    return DriveMover(layout=layout, env=env, accounts=Accounts(), timeout_s=5.0)


@pytest.mark.parametrize(
    "rule",
    [
        {"exit": 1, "stderr": [jl("Failed to lsjson: error 500", level="error")]},
        {"exit": 1, "stderr": [jl("Failed to lsjson: object not found", level="error")]},
        {"exit": 0, "stdout": ["not json"]},
        {"exit": 0, "stdout": []},
    ],
)
async def test_exists_fails_closed_on_unclear_answers(drive_mover, fake_rclone, rule):
    from app.services.files.ops import FileOpError

    fake_rclone.on("lsjson", **rule)
    acc = type("A", (), {"id": 1})()
    with pytest.raises(FileOpError) as e:
        await drive_mover.exists(acc, "gd_1:a.txt")
    assert e.value.status == 502


@pytest.mark.parametrize(("code", "found"), [(3, False), (4, False)])
async def test_exists_reads_rclone_not_found_exit_codes(drive_mover, fake_rclone, code, found):
    fake_rclone.on("lsjson", exit=code)
    assert await drive_mover.exists(type("A", (), {"id": 1})(), "gd_1:a.txt") is found


async def test_drive_calls_are_bounded_and_rclone_is_stopped(drive_mover, fake_rclone):
    import time

    from app.services.files.ops import FileOpError

    fake_rclone.on("moveto", sleep=20)
    started = time.monotonic()
    with pytest.raises(FileOpError) as e:
        await drive_mover.move(type("A", (), {"id": 1})(), "gd_1:a", "gd_1:b")
    assert e.value.status == 504
    assert time.monotonic() - started < 15


async def test_cancel_in_the_same_tick_as_thread_completion_still_counts(monkeypatch):
    import asyncio

    from app.services.files import moves

    async def cancel_after_done(task):
        await task  # the thread is finished ...
        raise asyncio.CancelledError()  # ... and the cancel lands in that same tick

    monkeypatch.setattr(moves.asyncio, "shield", cancel_after_done)
    result, error, cancelled = await moves.finish_in_thread(lambda: "ok")
    assert (result, error, cancelled) == ("ok", None, True)


ONE_WAY_MSG = (
    "This folder only receives changes from Google Drive; files added or changed here would "
    "be removed by the next sync."
)


@pytest.fixture
def one_way(auth_client, app, make_account, make_pair, stub_queue):
    acc = make_account()
    root = app.state.layout.account_dir(acc.email)
    down = make_pair(
        acc, name="Down", local_subpath="Down", direction="REMOTE_TO_LOCAL", initialized=True
    )
    up = make_pair(
        acc, name="Up", local_subpath="Up", direction="LOCAL_TO_REMOTE", initialized=True
    )
    for rel in ("Down/sub/a.txt", "Up/b.txt", "Loose/c.txt"):
        _put(root, rel)
    return acc, root, down, up


def test_remote_to_local_folders_refuse_every_local_change(auth_client, one_way, stub_queue):
    acc, root, _, _ = one_way
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    attempts = [
        _post(auth_client, "mkdir", account_id=acc.id, path="Down", name="New"),
        _post(auth_client, "rename", account_id=acc.id, path="Down/sub/a.txt", new_name="b.txt"),
        _post(auth_client, "move", account_id=acc.id, paths=["Down/sub/a.txt"], to="Loose"),
        _post(auth_client, "move", account_id=acc.id, paths=["Loose/c.txt"], to="Down/sub"),
        _post(auth_client, "delete", account_id=acc.id, paths=["Down/sub/a.txt"]),
        _post(auth_client, "new", account_id=acc.id, path="Down", kind="docx"),
        auth_client.post(
            "/api/files/upload",
            params={"account_id": acc.id, "path": "Down/sub", "name": "up.txt"},
            content=b"x",
        ),
        auth_client.post(  # a folder upload from above that lands inside the pair
            "/api/files/upload",
            params={"account_id": acc.id, "path": "", "name": "Down/new/up.txt"},
            content=b"x",
        ),
    ]
    for r in attempts:
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == ONE_WAY_MSG
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*")) == before
    assert stub_queue.requests == []


def test_local_to_remote_folders_take_adds_and_deletes(auth_client, one_way):
    acc, root, _, up = one_way
    r = _post(auth_client, "mkdir", account_id=acc.id, path="Up", name="New")
    assert r.status_code == 200, r.text
    r = _post(auth_client, "delete", account_id=acc.id, paths=["Up/b.txt"])
    assert r.status_code == 200, r.text


def test_browse_tells_the_folder_direction(auth_client, one_way):
    acc, _, down, _ = one_way
    r = auth_client.get("/api/browse", params={"account_id": acc.id, "path": "Down"})
    assert r.json()["pair"]["direction"] == "REMOTE_TO_LOCAL"


PENDING_MSG = "This has changes waiting to sync. Sync now, then try again."


@pytest.mark.parametrize("pending", ["upload", "download", "conflict"])
def test_drive_move_is_refused_while_changes_wait_to_sync(
    auth_client, app, env, fake_rclone, save_catalog, db, pending
):
    from app.models import Conflict

    acc, root, pair = env
    _put(root, "Drive/Old/x.txt")
    entries = [_entry("Old", is_dir=True), _entry("Old/x.txt")]
    if pending == "upload":
        _put(root, "Drive/Old/x.txt", b"edited here")
    elif pending == "download":
        entries.append(_entry("Old/only-on-drive.txt"))
    else:
        db.add(Conflict(pair_id=pair.id, path="Old/x.txt", local_variant="", remote_variant=""))
        db.commit()
    save_catalog(pair.id, entries)
    _stat_missing(fake_rclone)
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/Old", new_name="New")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == PENDING_MSG
    assert (root / "Drive/Old/x.txt").exists() and _moves(fake_rclone) == []
    r = _post(auth_client, "move", account_id=acc.id, paths=["Drive/Old/x.txt"], to="Drive")
    assert r.status_code == (409 if pending != "download" else 200), r.text


def test_answer_says_which_items_moved_on_drive_too(auth_client, env, fake_rclone, save_catalog):
    acc, root, pair = env
    _put(root, "Drive/on.txt")
    _put(root, "Drive/off.txt")
    (root / "Drive/Dest").mkdir()
    save_catalog(pair.id, [_entry("on.txt"), _entry("Dest", is_dir=True)])
    _stat_missing(fake_rclone)
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/on.txt", new_name="on2.txt")
    assert r.json()["drive"] == ["Drive/on2.txt"]
    paths = ["Drive/on2.txt", "Drive/off.txt"]
    r = _post(auth_client, "move", account_id=acc.id, paths=paths, to="Drive/Dest")
    assert r.status_code == 200, r.text
    # on2.txt: the cached listing already has its new name; off.txt isn't on Drive yet.
    assert r.json()["drive"] == ["Drive/Dest/on2.txt"]


def _catalog_paths(app, pair):
    cat = app.state.catalog.store.load(pair.id)
    return sorted([*cat.files, *cat.natives])


def test_done_actions_update_the_cached_listing(
    auth_client, app, env, fake_rclone, save_catalog, refreshes
):
    acc, root, pair = env
    for rel in ("Drive/Old/x.txt", "Drive/gone.txt"):
        _put(root, rel)
    save_catalog(
        pair.id,
        [
            _entry("Old", is_dir=True, id="F1"),
            _entry("Old/x.txt", id="X1"),
            CatalogEntry(path="Old/Plan.docx", is_dir=False, size=0, mtime=None, native="document"),
            _entry("gone.txt"),
        ],
    )
    _stat_missing(fake_rclone)
    assert (
        _post(auth_client, "delete", account_id=acc.id, paths=["Drive/gone.txt"]).status_code == 200
    )
    r = _post(auth_client, "rename", account_id=acc.id, path="Drive/Old", new_name="New")
    assert r.status_code == 200, r.text
    assert _catalog_paths(app, pair) == ["New", "New/Plan.docx", "New/x.txt"]
    assert app.state.catalog.store.load(pair.id).files["New/x.txt"].id == "X1"
    listed = auth_client.get("/api/browse", params={"account_id": acc.id, "path": "Drive"}).json()
    assert {i["name"]: i["status"] for i in listed["items"]} == {"New": "synced"}


def test_disk_gone_refuses_drive_moves_before_any_rclone_call(
    auth_client, app, make_account, make_pair, fake_rclone, save_catalog, monkeypatch
):
    # The real queue, and no sync has been asked for since the disk went away.
    from app.services.queue import MISSING_DISK_MSG

    acc = make_account()
    root = app.state.layout.account_dir(acc.email)
    pair = make_pair(acc, local_subpath="Drive", remote_path="", initialized=True)
    _put(root, "Drive/a.txt")
    (root / "Drive/Dest").mkdir()
    save_catalog(pair.id, [_entry("a.txt"), _entry("Dest", is_dir=True)])
    _stat_missing(fake_rclone)
    app.state.layout.volume_file.unlink()
    assert app.state.queue.disabled_reason is None
    for action, body in (
        ("rename", {"path": "Drive/a.txt", "new_name": "b.txt"}),
        ("move", {"paths": ["Drive/a.txt"], "to": "Drive/Dest"}),
        ("delete", {"paths": ["Drive/a.txt"]}),
        ("mkdir", {"path": "Drive", "name": "New"}),
    ):
        r = _post(auth_client, action, account_id=acc.id, **body)
        assert r.status_code == 503, (action, r.text)
        assert r.json()["detail"] == MISSING_DISK_MSG
    # A new Google file is made on Drive itself, and an upload ends up there: both refused.
    made: list[object] = []

    async def create_native(*args):
        made.append(args)
        return {"id": "G1"}

    monkeypatch.setattr(app.state.drive_api, "create_native", create_native)
    r = auth_client.post(
        "/api/files/new", json={"account_id": acc.id, "path": "Drive", "kind": "document"}
    )
    assert r.status_code == 503 and r.json()["detail"] == MISSING_DISK_MSG
    r = auth_client.post(
        "/api/files/upload",
        params={"account_id": acc.id, "path": "Drive", "name": "up.txt"},
        content=b"data",
    )
    assert r.status_code == 503 and r.json()["detail"] == MISSING_DISK_MSG
    assert made == []
    assert fake_rclone.calls() == []
    assert (root / "Drive/a.txt").exists() and not (root / "Drive/New").exists()
    assert not (root / "Drive/up.txt").exists()
    assert app.state.queue.disabled_reason == MISSING_DISK_MSG


NOT_SYNCED = "This folder isn't synced with Google Drive. Open a synced folder to add files."


@pytest.mark.parametrize(
    "call",
    [
        lambda c, a: c.post("/api/files/mkdir", json={"account_id": a, "path": "", "name": "New"}),
        lambda c, a: c.post("/api/files/new", json={"account_id": a, "path": "", "kind": "docx"}),
        lambda c, a: c.post(
            "/api/files/new", json={"account_id": a, "path": "", "kind": "document"}
        ),
        lambda c, a: c.post(
            "/api/files/upload",
            params={"account_id": a, "name": "Trip/day 1/a.txt"},
            content=b"x",
        ),
        lambda c, a: c.post(
            "/api/files/upload",
            params={"account_id": a, "path": "Loose", "name": "a.txt"},
            content=b"x",
        ),
    ],
)
def test_nothing_new_outside_synced_folders(auth_client, env, stub_queue, call):
    acc, root, _ = env
    (root / "Loose").mkdir()
    before = sorted(p.name for p in root.rglob("*"))
    r = call(auth_client, acc.id)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == NOT_SYNCED
    assert sorted(p.name for p in root.rglob("*")) == before  # not even a folder was made
    assert stub_queue.requests == []


def test_move_into_an_unsynced_folder_is_refused(auth_client, env, fake_rclone, stub_queue):
    acc, root, _ = env
    _put(root, "Drive/a.txt")
    (root / "Loose").mkdir()
    r = _post(auth_client, "move", account_id=acc.id, paths=["Drive/a.txt"], to="Loose")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == NOT_SYNCED
    assert (root / "Drive/a.txt").exists() and not (root / "Loose/a.txt").exists()
    assert fake_rclone.calls() == [] and stub_queue.requests == []


def test_adding_still_works_in_a_whole_account_pair_and_deep_folders(
    auth_client, app, make_account, make_pair, stub_queue
):
    whole = make_account(email="whole@example.com")
    root = app.state.layout.account_dir(whole.email)
    make_pair(whole, name="All", local_subpath="", remote_path="", initialized=True)
    deep = make_account(email="deep@example.com")
    droot = app.state.layout.account_dir(deep.email)
    make_pair(deep, name="D", local_subpath="A", remote_path="", initialized=True)
    (droot / "A/B/C").mkdir(parents=True)
    for acc, folder in ((whole, ""), (deep, "A/B/C")):
        r = _post(auth_client, "mkdir", account_id=acc.id, path=folder, name="Made")
        assert r.status_code == 200, r.text
        r = _post(auth_client, "new", account_id=acc.id, path=folder, kind="docx")
        assert r.status_code == 200, r.text
        r = auth_client.post(
            "/api/files/upload",
            params={"account_id": acc.id, "path": folder, "name": "u.txt"},
            content=b"x",
        )
        assert r.status_code == 200, r.text
    assert (root / "Made").is_dir() and (droot / "A/B/C/u.txt").exists()
