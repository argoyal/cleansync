import json


def test_remote_browse_lists_folders(auth_client, make_account, fake_rclone):
    acc = make_account()
    fake_rclone.on(
        "lsjson",
        stdout=[
            json.dumps(
                [
                    {"Path": "Work", "Name": "Work", "IsDir": True},
                    {"Path": "Archive", "Name": "Archive", "IsDir": True},
                ]
            )
        ],
    )
    r = auth_client.get(f"/api/remote-browse?account_id={acc.id}&path=")
    assert r.json() == [{"name": "Archive", "path": "Archive"}, {"name": "Work", "path": "Work"}]
    call = fake_rclone.calls()[-1]
    assert call[4:6] == ["lsjson", f"{acc.remote_name}:"] and "--dirs-only" in call


def test_remote_browse_subfolder_paths(auth_client, make_account, fake_rclone):
    acc = make_account()
    fake_rclone.on(
        "lsjson", stdout=[json.dumps([{"Path": "Projects", "Name": "Projects", "IsDir": True}])]
    )
    r = auth_client.get(f"/api/remote-browse?account_id={acc.id}&path=Work")
    assert r.json() == [{"name": "Projects", "path": "Work/Projects"}]


def test_remote_browse_error_is_reported(auth_client, make_account, fake_rclone):
    acc = make_account()
    fake_rclone.on("lsjson", stderr=['{"level":"error","msg":"directory not found"}'], exit=3)
    r = auth_client.get(f"/api/remote-browse?account_id={acc.id}&path=Nope")
    assert r.status_code == 502


def test_local_browse_stays_inside_account(auth_client, app, make_account):
    acc = make_account()
    root = app.state.layout.storage / "alice@example.com"
    (root / "B").mkdir()
    (root / "A").mkdir()
    (root / ".google-exports").mkdir()
    (root / "file.txt").write_text("x")
    r = auth_client.get(f"/api/local-browse?account_id={acc.id}&path=")
    assert r.json() == [{"name": "A", "path": "A"}, {"name": "B", "path": "B"}]
    assert auth_client.get(f"/api/local-browse?account_id={acc.id}&path=../..").status_code == 422
