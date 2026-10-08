import shutil
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, SyncPair
from app.paths import Layout


def prune_backups(layout: Layout, db: Session, today: date) -> int:
    """Remove dated backup folders older than each pair's retention. Returns folders removed."""
    removed = 0
    for pair in db.scalars(select(SyncPair)):
        if pair.backup_retention_days is None:
            continue
        account = db.get(Account, pair.account_id)
        root = layout.backups_dir / account.email / str(pair.id)
        if not root.is_dir():
            continue
        cutoff = today - timedelta(days=pair.backup_retention_days)
        for d in root.iterdir():
            try:
                day = date.fromisoformat(d.name)
            except ValueError:
                continue
            if d.is_dir() and not d.is_symlink() and day < cutoff:
                shutil.rmtree(d)
                removed += 1
    return removed
