import stat

import pytest

from app.paths import Layout


def test_layout_paths_follow_spec(tmp_path):
    lay = Layout(tmp_path)
    assert lay.data == tmp_path / "app" / "data"
    assert lay.db_file == tmp_path / "app" / "data" / "drivesync.db"
    assert lay.rclone_conf == tmp_path / "app" / "data" / "rclone" / "rclone.conf"
    assert lay.storage == tmp_path / "storage"
    assert lay.pair_workdir(3) == tmp_path / "app" / "data" / "rclone" / "bisync" / "3"
    assert lay.pair_filters(3) == tmp_path / "app" / "data" / "filters" / "3.txt"
    assert lay.job_log(7) == tmp_path / "app" / "data" / "logs" / "jobs" / "7.log"


def test_ensure_creates_directories(tmp_path):
    lay = Layout(tmp_path)
    lay.ensure()
    for d in (
        lay.db_backups,
        lay.rclone_dir,
        lay.bisync_dir,
        lay.filters_dir,
        lay.catalog_dir,
        lay.previews_dir,
        lay.backups_dir,
        lay.thumbs_dir,
        lay.jobs_log_dir,
        lay.storage,
        lay.secrets_dir,
    ):
        assert d.is_dir()
    assert stat.S_IMODE(lay.secrets_dir.stat().st_mode) == 0o700


def test_ensure_tightens_existing_secrets_dir(tmp_path):
    lay = Layout(tmp_path)
    lay.secrets_dir.mkdir(parents=True, mode=0o755)
    lay.secrets_dir.chmod(0o755)
    lay.ensure()
    assert stat.S_IMODE(lay.secrets_dir.stat().st_mode) == 0o700


def test_account_dir_uses_lowercased_email(tmp_path):
    assert (
        Layout(tmp_path).account_dir("Alice@Example.com")
        == tmp_path / "storage" / "alice@example.com"
    )


@pytest.mark.parametrize("bad", ["../x@example.com", "a/b@example.com", "not-an-email", ""])
def test_account_dir_rejects_bad_email(tmp_path, bad):
    with pytest.raises(ValueError):
        Layout(tmp_path).account_dir(bad)
