import os
import secrets
from pathlib import Path


def resolve_secret(value: str, file: Path) -> str:
    """Use the env value if given; else reuse the saved one; else generate and save it (mode 600)."""
    if value:
        return value
    if file.exists():
        return file.read_text().strip()
    file.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(48)
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(generated)
    return generated
