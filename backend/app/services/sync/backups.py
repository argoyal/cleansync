import logging
import shutil
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, SyncPair
from app.paths import Layout
from app.services.app_settings import get_setting

log = logging.getLogger(__name__)

# Recovery for items deleted in the UI outside any sync pair: backups/<email>/unsynced/<date>/
UNSYNCED = "unsynced"


def _prune_dated(root: Path, cutoff: date) -> int:
    if not root.is_dir():
        return 0
    removed = 0
    for d in root.iterdir():
        try:
            day = date.fromisoformat(d.name)
        except ValueError:
            continue
        if d.is_dir() and not d.is_symlink() and day < cutoff:
            try:
                shutil.rmtree(d)
            except OSError:
                log.exception("could not remove expired backup folder %s", d)
                continue
            removed += 1
    return removed


def prune_backups(layout: Layout, db: Session, today: date) -> int:
    """Remove dated backup folders older than their retention. Returns folders removed.

    Each pair keeps its own retention; the "unsynced" Recovery folder follows the default
    retention setting. No retention (None) keeps everything.
    """
    removed = 0
    for pair in db.scalars(select(SyncPair)):
        if pair.backup_retention_days is None:
            continue
        account = db.get(Account, pair.account_id)
        cutoff = today - timedelta(days=pair.backup_retention_days)
        removed += _prune_dated(layout.backups_dir / account.email / str(pair.id), cutoff)
    days = get_setting(db, "default_backup_retention_days")
    if days is not None:
        cutoff = today - timedelta(days=int(days))  # type: ignore[arg-type]
        for account in db.scalars(select(Account)):
            removed += _prune_dated(layout.backups_dir / account.email / UNSYNCED, cutoff)
    return removed
