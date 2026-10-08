import logging
import re
from collections.abc import Iterable

_MASK = "***"
# Extra secrets shorter than this are ignored: a 3-character "secret" would mask
# ordinary words and digits everywhere in a log line.
_MIN_EXTRA_LEN = 6

_KEY = (
    r'\\?"?(?:access_token|refresh_token|id_token|client_secret|password|passwd|pass|'
    r"secret|token|authorization|rclone_config_pass)\\?\"?"
)
# A quoted value may contain backslash-escaped quotes; an unquoted value ends at space, comma or brace.
_VALUE = r'(?:\\?"(?:[^"\\]|\\.)*\\?"|[^\s,}"\\]+)'

# Header-style and bare-token patterns run first, so a "Bearer <token>" pair is
# masked as a whole before the key/value pattern sees the word "Bearer" alone.
_TOKEN_PATTERNS = [
    re.compile(r"(Authorization:\s*Basic\s+)\S+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)\S+", re.IGNORECASE),
    re.compile(r"(GOCSPX-)[\w-]+"),
    re.compile(r"()ya29\.[0-9A-Za-z_\-\.]+"),
    re.compile(r"()1//[0-9A-Za-z_\-]{10,}"),
]
_KEY_VALUE_PATTERN = re.compile(r"(" + _KEY + r"\s*[:=]\s*)(" + _VALUE + r")", re.IGNORECASE)


def redact(text: str, extra: Iterable[str] = ()) -> str:
    for secret in extra:
        if secret and len(secret) >= _MIN_EXTRA_LEN:
            text = text.replace(secret, _MASK)
    for pat in _TOKEN_PATTERNS:
        text = pat.sub(lambda m: m.group(1) + _MASK, text)
    return _KEY_VALUE_PATTERN.sub(lambda m: m.group(1) + _MASK, text)


class RedactingFilter(logging.Filter):
    """Masks secrets in a log record: message, args, exception text and stack info.

    Attach this filter to handlers, not loggers. A logger-level filter does not
    run for records that child loggers pass up to parent handlers.

    An exception traceback is formatted here and stored as ``exc_text``, so
    ``exc_info`` is cleared; this runs before any handler formats the record.
    """

    def __init__(self) -> None:
        super().__init__()
        self._secrets: set[str] = set()

    def add_secret(self, value: str) -> None:
        if value:
            self._secrets.add(value)

    def filter(self, record: logging.LogRecord) -> bool:
        # Snapshot the set so a concurrent add_secret() cannot change it mid-redaction.
        secrets = tuple(self._secrets)
        if record.exc_info:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text, secrets)
        if record.stack_info:
            record.stack_info = redact(record.stack_info, secrets)
        record.msg = redact(record.getMessage(), secrets)
        record.args = None
        return True


# Request paths whose query string carries secrets (the OAuth code and state).
_SECRET_QUERY_PATHS = ("/api/oauth/callback",)


class AccessLogQueryFilter(logging.Filter):
    """Drops the query string of secret-carrying paths from uvicorn's access log.

    Attach to the ``uvicorn.access`` logger: its records are created there. The record keeps
    uvicorn's argument tuple (client, method, path, http version, status), which uvicorn's
    AccessFormatter needs, so only the path inside it changes.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path, sep, _query = args[2].partition("?")
            if sep and path.endswith(_SECRET_QUERY_PATHS):
                record.args = (*args[:2], f"{path}?<redacted>", *args[3:])
        return True
