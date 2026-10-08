import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 12
_ph = PasswordHasher()
# Argon2 is memory-hungry; cap concurrent hashing so unauthenticated floods can't exhaust RAM.
_guard = threading.BoundedSemaphore(2)
_DUMMY = _ph.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    with _guard:
        return _ph.hash(password)


def verify_password(hashed: str | None, password: str) -> bool:
    try:
        with _guard:
            return _ph.verify(hashed or _DUMMY, password) and hashed is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
