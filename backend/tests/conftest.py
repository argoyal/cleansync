from pathlib import Path

import pytest

from app.config import Settings
from app.paths import Layout


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        drivesync_root=tmp_path / "root",
        public_url="http://localhost:8080",
        admin_username="admin",
        admin_password="initial-pass-123",
        google_client_id="cid",
        google_client_secret="csecret",
    )


@pytest.fixture
def layout(settings: Settings) -> Layout:
    lay = Layout(settings.drivesync_root)
    lay.ensure()
    return lay
