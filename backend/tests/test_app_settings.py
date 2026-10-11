import pytest

from app.services.app_settings import DEFAULTS, get_all, get_setting, set_settings


def test_defaults_match_spec(db):
    assert DEFAULTS == {
        "default_interval_s": 300,
        "default_max_deletions": 10,
        "default_backups_enabled": True,
        "default_backup_retention_days": 30,
        "watcher_debounce_s": 5,
        "max_parallel_pairs": 2,
        "session_idle_hours": 12,
        "log_retention_days": 30,
        "mail_check_interval_s": 300,
        "mail_min_age_days": 30,
        "mail_batch_size": 100,
        "mail_disk_floor_gb": 2,
        "google_daily_budget_units": 10_000_000,
    }
    assert get_setting(db, "max_parallel_pairs") == 2


def test_set_and_read_back(db):
    set_settings(db, {"default_max_deletions": 25})
    assert get_setting(db, "default_max_deletions") == 25
    assert get_all(db)["default_max_deletions"] == 25


@pytest.mark.parametrize(
    "bad",
    [
        {"nope": 1},
        {"max_parallel_pairs": "two"},
        {"default_interval_s": 42},
        {"default_backup_retention_days": 3},
        {"max_parallel_pairs": 0},
    ],
)
def test_invalid_values_rejected(db, bad):
    with pytest.raises(ValueError):
        set_settings(db, bad)


@pytest.mark.parametrize(
    "bad",
    [
        {"default_interval_s": 60.0},
        {"default_interval_s": True},
        {"default_backup_retention_days": 30.0},
        {"default_backup_retention_days": True},
        {"session_idle_hours": 721},
        {"watcher_debounce_s": 301},
        {"max_parallel_pairs": 9},
        {"default_max_deletions": 1_000_001},
        {"log_retention_days": 3651},
        {"mail_check_interval_s": 42},
        {"mail_batch_size": 0},
        {"mail_batch_size": 1001},
        {"mail_min_age_days": 3651},
        {"mail_disk_floor_gb": 10_001},
        {"google_daily_budget_units": 80_000_001},
    ],
)
def test_non_int_and_oversized_rejected(db, bad):
    with pytest.raises(ValueError):
        set_settings(db, bad)


def test_upper_bounds_are_accepted(db):
    ok = {
        "session_idle_hours": 720,
        "watcher_debounce_s": 300,
        "max_parallel_pairs": 8,
        "default_max_deletions": 1_000_000,
        "log_retention_days": 3650,
        "default_interval_s": None,
    }
    assert set_settings(db, ok)["max_parallel_pairs"] == 8
