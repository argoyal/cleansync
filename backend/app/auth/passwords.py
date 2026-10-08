from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 12
_ph = PasswordHasher()
_DUMMY = _ph.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(hashed: str | None, password: str) -> bool:
    try:
        return _ph.verify(hashed or _DUMMY, password) and hashed is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
