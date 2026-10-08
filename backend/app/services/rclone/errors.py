from enum import StrEnum


class Failure(StrEnum):
    AUTH = "AUTH"
    ACCESS_CHECK = "ACCESS_CHECK"
    NEEDS_RESYNC = "NEEDS_RESYNC"
    TRANSIENT = "TRANSIENT"
    OTHER = "OTHER"


_AUTH = (
    "invalid_grant",
    "oauth2: cannot fetch token",
    "token has been expired or revoked",
    "couldn't fetch token",
    "unauthorized_client",
    "invalid_client",
)
_ACCESS = ("check file check failed", "access test failed")
_CONFIG = ("specified filters file does not exist", "unknown flag")
_RESYNC = ("must run --resync",)
_TRANSIENT = (
    "ratelimitexceeded",
    "userratelimitexceeded",
    "connection reset",
    "no such host",
    "i/o timeout",
    "error 500",
    "error 502",
    "error 503",
    "temporary failure",
    "tls handshake timeout",
    "context deadline exceeded",
)


def classify_failure(exit_code: int, error_tail: list[str]) -> Failure | None:
    if exit_code == 0:
        return None
    text = "\n".join(error_tail).lower()
    if any(m in text for m in _AUTH):
        return Failure.AUTH
    if any(m in text for m in _CONFIG) or any(
        ln.lstrip().startswith("Error:") for ln in error_tail
    ):
        return Failure.OTHER
    if any(m in text for m in _ACCESS):
        return Failure.ACCESS_CHECK
    if any(m in text for m in _RESYNC):
        return Failure.NEEDS_RESYNC
    if exit_code == 5 or any(m in text for m in _TRANSIENT):
        return Failure.TRANSIENT
    return Failure.OTHER
