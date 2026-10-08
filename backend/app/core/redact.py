import logging
import re
from collections.abc import Iterable

_MASK = "***"
_PATTERNS = [
    re.compile(
        r'("?(?:access_token|refresh_token|id_token|client_secret|password)"?\s*[:=]\s*)'
        r'("[^"]*"|[^\s,}]+)',
        re.IGNORECASE,
    ),
    re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.IGNORECASE),
    re.compile(r"()ya29\.[0-9A-Za-z_\-\.]+"),
    re.compile(r"()1//[0-9A-Za-z_\-]{10,}"),
]


def redact(text: str, extra: Iterable[str] = ()) -> str:
    for secret in extra:
        if secret:
            text = text.replace(secret, _MASK)
    for pat in _PATTERNS:
        text = pat.sub(lambda m: m.group(1) + _MASK, text)
    return text


class RedactingFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._secrets: set[str] = set()

    def add_secret(self, value: str) -> None:
        if value:
            self._secrets.add(value)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self._secrets)
        record.args = None
        return True
