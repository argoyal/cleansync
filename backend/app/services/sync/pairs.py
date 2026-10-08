import shutil
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import as_utc
from app.core.pathsafe import UnsafePath, normalize_rel, safe_join, validate_remote_path
from app.models import Account, SyncPair
from app.models.enums import PairStatus
from app.paths import Layout
from app.schemas.pairs import _UNSET, PairCreate, PairOut, PairUpdate
from app.services.app_settings import get_setting
from app.services.rclone.commands import remote_target
from app.services.sync.filters import refresh_pair_filters


class PairError(ValueError):
    pass


def _overlaps(a: str, b: str) -> bool:
    return a == b or a == "" or b == "" or a.startswith(b + "/") or b.startswith(a + "/")


def pair_local_dir(layout: Layout, account: Account, pair: SyncPair) -> Path:
    return safe_join(layout.account_dir(account.email), pair.local_subpath)


def pair_remote(account: Account, pair: SyncPair) -> str:
    return remote_target(account.remote_name, pair.remote_path)


def next_sync_at(pair: SyncPair) -> datetime | None:
    if pair.schedule_interval_s is None or pair.last_sync_at is None or pair.paused:
        return None
    return as_utc(pair.last_sync_at) + timedelta(seconds=pair.schedule_interval_s)


def _check_overlap(
    db: Session, account_id: int, local: str, remote: str, exclude_id: int | None = None
) -> None:
    for other in db.scalars(select(SyncPair).where(SyncPair.account_id == account_id)):
        if other.id == exclude_id:
            continue
        if _overlaps(local, other.local_subpath):
            raise PairError(f"local folder overlaps the folder of pair '{other.name}'")
        if _overlaps(remote, other.remote_path):
            raise PairError(f"Google Drive folder overlaps the folder of pair '{other.name}'")


def create_pair(db: Session, layout: Layout, data: PairCreate) -> SyncPair:
    acc = db.get(Account, data.account_id)
    if acc is None:
        raise PairError("account not found")
    try:
        local = normalize_rel(data.local_subpath)
        remote = validate_remote_path(data.remote_path)
        local_dir = safe_join(layout.account_dir(acc.email), local)
    except UnsafePath as e:
        raise PairError(str(e)) from e
    _check_overlap(db, acc.id, local, remote)
    local_dir.mkdir(parents=True, exist_ok=True)
    pair = SyncPair(
        account_id=acc.id,
        name=data.name,
        local_subpath=local,
        remote_path=remote,
        direction=data.direction,
        schedule_interval_s=(
            get_setting(db, "default_interval_s")
            if data.schedule_interval_s == _UNSET
            else data.schedule_interval_s
        ),
        max_deletions=(
            get_setting(db, "default_max_deletions")
            if data.max_deletions is None
            else data.max_deletions
        ),
        backups_enabled=(
            get_setting(db, "default_backups_enabled")
            if data.backups_enabled is None
            else data.backups_enabled
        ),
        backup_retention_days=(
            get_setting(db, "default_backup_retention_days")
            if data.backup_retention_days == _UNSET
            else data.backup_retention_days
        ),
        status=PairStatus.NEW,
    )
    db.add(pair)
    db.flush()
    # an explicit None (manual / never delete) would be replaced by the column default on INSERT
    pair.schedule_interval_s = (
        get_setting(db, "default_interval_s")
        if data.schedule_interval_s == _UNSET
        else data.schedule_interval_s
    )
    pair.backup_retention_days = (
        get_setting(db, "default_backup_retention_days")
        if data.backup_retention_days == _UNSET
        else data.backup_retention_days
    )
    db.commit()
    refresh_pair_filters(db, layout, pair)
    return pair


def update_pair(db: Session, pair: SyncPair, data: PairUpdate) -> SyncPair:
    if data.name is not None:
        pair.name = data.name
    if data.schedule_interval_s != _UNSET:
        pair.schedule_interval_s = data.schedule_interval_s
    if data.max_deletions is not None:
        pair.max_deletions = data.max_deletions
    if data.backups_enabled is not None:
        pair.backups_enabled = data.backups_enabled
    if data.backup_retention_days != _UNSET:
        pair.backup_retention_days = data.backup_retention_days
    db.commit()
    return pair


def delete_pair(db: Session, layout: Layout, pair: SyncPair) -> None:
    shutil.rmtree(layout.pair_workdir(pair.id), ignore_errors=True)
    layout.pair_filters(pair.id).unlink(missing_ok=True)
    db.delete(pair)
    db.commit()


def to_out(layout: Layout, account: Account, pair: SyncPair) -> PairOut:
    return PairOut(
        id=pair.id,
        account_id=account.id,
        account_email=account.email,
        name=pair.name,
        local_subpath=pair.local_subpath,
        local_path=str(pair_local_dir(layout, account, pair)),
        remote_path=pair.remote_path,
        direction=pair.direction,
        schedule_interval_s=pair.schedule_interval_s,
        paused=pair.paused,
        max_deletions=pair.max_deletions,
        backups_enabled=pair.backups_enabled,
        backup_retention_days=pair.backup_retention_days,
        initialized=pair.initialized,
        status=pair.status,
        attention_reason=pair.attention_reason,
        last_error=pair.last_error,
        last_sync_at=pair.last_sync_at,
        last_success_at=pair.last_success_at,
        last_error_at=pair.last_error_at,
        next_sync_at=next_sync_at(pair),
    )
