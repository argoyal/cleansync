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
