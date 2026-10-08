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
