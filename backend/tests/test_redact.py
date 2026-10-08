import io
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


# --- fix round 1: adversarial cases ---


def test_escaped_json_nested_token_is_redacted():
    text = '{"token":"{\\"access_token\\":\\"AAA\\",\\"client_secret\\":\\"SEC\\"}"}'
    out = redact(text)
    assert "AAA" not in out
    assert "SEC" not in out


def test_quoted_value_with_escaped_quote_is_fully_redacted():
    out = redact('password="ab\\"cdSECRET"')
    assert "SECRET" not in out


def test_rclone_config_pass_env_form_is_redacted():
    assert "hunter2" not in redact("RCLONE_CONFIG_PASS=hunter2")


def test_rclone_conf_pass_line_is_redacted():
    out = redact("pass = abcdEFGH")
    assert "abcdEFGH" not in out


def test_bare_gocspx_client_secret_is_redacted():
    out = redact("GOCSPX-abc_123")
    assert "abc_123" not in out


def test_basic_auth_header_is_redacted():
    out = redact("Authorization: Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in out


def test_bearer_anywhere_is_redacted():
    out = redact("access_token: Bearer abcTOKEN123")
    assert "abcTOKEN123" not in out


def test_extra_secret_shorter_than_six_chars_is_ignored():
    assert redact("code 12345", extra=["123"]) == "code 12345"


def test_extra_secret_of_six_chars_is_redacted():
    assert "abc123" not in redact("value abc123 here", extra=["abc123"])


def test_logger_exception_traceback_is_redacted():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("test_redact.exception")
    logger.setLevel(logging.ERROR)
    logger.addHandler(handler)
    logger.propagate = False
    try:
        try:
            raise ValueError("refresh_token=LEAKEDTOKEN987654")
        except ValueError:
            logger.exception("sync failed for token=ya29.MSGTOKEN123")
    finally:
        logger.removeHandler(handler)
    output = stream.getvalue()
    assert "sync failed" in output
    assert "LEAKEDTOKEN" not in output
    assert "MSGTOKEN" not in output


def test_json_stays_valid_after_redaction():
    import json

    out = redact('{"level":"error","msg":"token: abc"}')
    assert json.loads(out)["level"] == "error"
