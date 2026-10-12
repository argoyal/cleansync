import logging
import sys
from collections.abc import Iterable

from app.core.redact import AccessLogQueryFilter, RedactingFilter

_HANDLER_NAME = "drivesync"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _StderrHandler(logging.StreamHandler):
    """Writes to whatever sys.stderr is at the time (tests and servers may swap it)."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        pass


def configure_logging(level: str, secrets: Iterable[str]) -> RedactingFilter:
    """Root logging to stderr at `level`, every record masked. Safe to call more than once."""
    root = logging.getLogger()
    handler = next((h for h in root.handlers if h.get_name() == _HANDLER_NAME), None)
    if handler is None:
        handler = _StderrHandler()
        handler.set_name(_HANDLER_NAME)
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    # uvicorn's access log has its own handler and does not reach ours.
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessLogQueryFilter) for f in access.filters):
        access.addFilter(AccessLogQueryFilter())
    # httpx logs every request's URL at INFO (model addresses included): warnings only.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    redactor = next(f for f in handler.filters if isinstance(f, RedactingFilter))
    for secret in secrets:
        redactor.add_secret(secret)
    name = level.strip().upper()
    if not isinstance(logging.getLevelName(name), int):
        root.setLevel(logging.INFO)
        logging.getLogger(__name__).warning("unknown LOG_LEVEL %r; using INFO", level)
    else:
        root.setLevel(name)
    return redactor
