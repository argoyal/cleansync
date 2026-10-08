import logging

from app.core.redact import RedactingFilter, redact


def test_rclone_conf_token_line_is_redacted():
    line = (
        'token = {"access_token":"ya29.a0AfB_byC","token_type":"Bearer",'
        '"refresh_token":"1//0gLongRefreshTokenValue1234567890","expiry":"2026-10-08T10:00:00Z"}'
    )
    out = redact(line)
    assert "ya29" not in out
    assert "1//0gLong" not in out
    assert "expiry" in out


def test_json_and_header_forms_are_redacted():
    assert "s3cr3t" not in redact('{"client_secret": "s3cr3t"}')
    assert "abc.def" not in redact("Authorization: Bearer abc.def")
    assert "XYZ" not in redact("client_secret=XYZ other=1")


def test_extra_secrets_are_redacted():
    assert "hunter2hunter2" not in redact("password is hunter2hunter2", extra=["hunter2hunter2"])


def test_logging_filter_redacts_messages_and_args():
    f = RedactingFilter()
    f.add_secret("my-config-pass")
    rec = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "pass=%s tok=%s", ("my-config-pass", "ya29.zzz"), None
    )
    f.filter(rec)
    msg = rec.getMessage()
    assert "my-config-pass" not in msg
    assert "ya29.zzz" not in msg
