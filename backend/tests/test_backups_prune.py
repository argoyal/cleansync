from datetime import date

from app.services.sync.backups import prune_backups


def test_prune_removes_only_expired_dated_folders(layout, db, make_account, make_pair):
    acc = make_account()
    keep30 = make_pair(acc, backup_retention_days=30)
    forever = make_pair(acc, backup_retention_days=None, local_subpath="f", remote_path="f")
    for pair in (keep30, forever):
        for d in ("2026-08-01", "2026-10-01", "not-a-date"):
            (layout.backups_dir / acc.email / str(pair.id) / d).mkdir(parents=True)
    removed = prune_backups(layout, db, today=date(2026, 10, 8))
    assert removed == 1
    left = sorted(p.name for p in (layout.backups_dir / acc.email / str(keep30.id)).iterdir())
    assert left == ["2026-10-01", "not-a-date"]
    assert len(list((layout.backups_dir / acc.email / str(forever.id)).iterdir())) == 3


def test_prune_keeps_going_when_one_folder_cannot_be_removed(
    layout, db, make_account, make_pair, monkeypatch
):
    from app.services.sync import backups as backups_mod

    acc = make_account()
    pair = make_pair(acc, backup_retention_days=7)
    root = layout.backups_dir / acc.email / str(pair.id)
    for d in ("2026-01-01", "2026-01-02"):
        (root / d).mkdir(parents=True)
    real = backups_mod.shutil.rmtree

    def flaky(path, *a, **kw):
        if path.name == "2026-01-01":
            raise PermissionError("nope")
        real(path, *a, **kw)

    monkeypatch.setattr(backups_mod.shutil, "rmtree", flaky)
    assert prune_backups(layout, db, today=date(2026, 10, 8)) == 1
    assert sorted(p.name for p in root.iterdir()) == ["2026-01-01"]


def test_prune_applies_the_default_retention_to_unsynced_recovery(
    layout, db, make_account, make_pair
):
    from app.services.app_settings import set_settings

    acc = make_account()
    root = layout.backups_dir / acc.email / "unsynced"
    for d in ("2026-08-01", "2026-10-01", "not-a-date"):
        (root / d).mkdir(parents=True)
    set_settings(db, {"default_backup_retention_days": 30})
    assert prune_backups(layout, db, today=date(2026, 10, 8)) == 1
    assert sorted(p.name for p in root.iterdir()) == ["2026-10-01", "not-a-date"]
    set_settings(db, {"default_backup_retention_days": None})  # keep forever
    assert prune_backups(layout, db, today=date(2027, 10, 8)) == 0
    assert sorted(p.name for p in root.iterdir()) == ["2026-10-01", "not-a-date"]
