"""I6: create_app configures logging once, at LOG_LEVEL, with secrets masked."""

import logging

import pytest

from app.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def _restore_root_level():
    root = logging.getLogger()
    level = root.level
    yield
    root.setLevel(level)


def _ours(root: logging.Logger) -> list[logging.Handler]:
    return [h for h in root.handlers if h.get_name() == "drivesync"]


def test_log_records_are_masked_and_the_level_applies(settings, capsys):
    s = Settings(**(settings.model_dump() | {"log_level": "DEBUG"}))
    app = create_app(s, start_background=False)
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    config_pass = app.state.rclone_env.config_pass
    assert len(config_pass) >= 6
    logging.getLogger("app.test").debug("rclone said %s and csecret-xyz", config_pass)
    err = capsys.readouterr().err
    assert "rclone said ***" in err
    assert config_pass not in err


def test_client_secret_and_admin_password_are_masked(settings, capsys):
    s = Settings(
        **(
            settings.model_dump()
            | {"google_client_secret": "GOOGLE-SECRET-VALUE", "admin_password": "admin-pw-123456"}
        )
    )
    create_app(s, start_background=False)
    logging.getLogger("app.test").warning("GOOGLE-SECRET-VALUE / admin-pw-123456")
    err = capsys.readouterr().err
    assert "*** / ***" in err
    assert "GOOGLE-SECRET-VALUE" not in err and "admin-pw-123456" not in err


def test_logging_is_configured_once(settings):
    create_app(settings, start_background=False)
    create_app(settings, start_background=False)
    assert len(_ours(logging.getLogger())) == 1


def test_info_level_hides_debug(settings, capsys):
    create_app(settings, start_background=False)
    assert logging.getLogger().level == logging.INFO
    logging.getLogger("app.test").debug("quiet please")
    assert "quiet please" not in capsys.readouterr().err
