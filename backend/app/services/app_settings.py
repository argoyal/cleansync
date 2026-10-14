from sqlalchemy.orm import Session

from app.models import SettingRow

DEFAULTS: dict[str, object] = {
    "default_interval_s": 300,
    "default_max_deletions": 10,
    "default_backups_enabled": True,
    "default_backup_retention_days": 30,
    "watcher_debounce_s": 5,
    "max_parallel_pairs": 2,
    "session_idle_hours": 12,
    "log_retention_days": 30,
    # Mail (spec §8)
    "mail_check_interval_s": 300,
    "mail_min_age_days": 30,
    "mail_disk_floor_gb": 2,
    # Google API (spec §7): one eighth of Google's 80M free line
    "google_daily_budget_units": 10_000_000,
}

INTERVAL_CHOICES = {60, 300, 900, 1800, 3600, None}
RETENTION_CHOICES = {7, 30, 90, None}
MAIL_INTERVAL_CHOICES = {60, 300, 900, 1800, 3600}


MAX_VALUES = {
    "session_idle_hours": 720,
    "watcher_debounce_s": 300,
    "max_parallel_pairs": 8,
    "default_max_deletions": 1_000_000,
    "log_retention_days": 3650,
    "mail_min_age_days": 3650,
    "mail_disk_floor_gb": 10_000,
    "google_daily_budget_units": 40_000_000,  # half of Google's 80M paid line
}


def _validate(key: str, value: object) -> None:
    if key not in DEFAULTS:
        raise ValueError(f"unknown setting {key!r}")
    if key in ("default_interval_s", "default_backup_retention_days"):
        if value is not None and type(value) is not int:
            raise ValueError(f"{key} must be an integer or null")
    if key == "default_interval_s":
        if value not in INTERVAL_CHOICES:
            raise ValueError("interval must be one of 60, 300, 900, 1800, 3600 or null")
        return
    if key == "default_backup_retention_days":
        if value not in RETENTION_CHOICES:
            raise ValueError("retention must be 7, 30, 90 or null")
        return
    if key == "mail_check_interval_s":
        if type(value) is not int or value not in MAIL_INTERVAL_CHOICES:
            raise ValueError("mail check interval must be one of 60, 300, 900, 1800 or 3600")
        return
    expected = type(DEFAULTS[key])
    if type(value) is not expected:
        raise ValueError(f"{key} must be {expected.__name__}")
    if expected is int and value < 1 and key != "default_max_deletions":  # type: ignore[operator]
        raise ValueError(f"{key} must be at least 1")
    if key == "default_max_deletions" and value < 0:  # type: ignore[operator]
        raise ValueError("default_max_deletions cannot be negative")
    if key in MAX_VALUES and value > MAX_VALUES[key]:  # type: ignore[operator]
        raise ValueError(f"{key} must be at most {MAX_VALUES[key]}")


def get_setting(db: Session, key: str) -> object:
    row = db.get(SettingRow, key)
    return DEFAULTS[key] if row is None else row.value


def get_all(db: Session) -> dict[str, object]:
    return {k: get_setting(db, k) for k in DEFAULTS}


REMOVED = (
    "mail_batch_size",
)  # 0.5.2: removed; still accepted and ignored so an old open tab can save


def set_settings(db: Session, values: dict[str, object]) -> dict[str, object]:
    values = {k: v for k, v in values.items() if k not in REMOVED}
    for k, v in values.items():
        _validate(k, v)
    for k, v in values.items():
        row = db.get(SettingRow, k)
        if row is None:
            db.add(SettingRow(key=k, value=v))
        else:
            row.value = v
    db.commit()
    return get_all(db)
