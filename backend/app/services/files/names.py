import re
from pathlib import Path, PurePosixPath

from app.core.pathsafe import UnsafePath
from app.services.files.scope import is_hidden_name

MAX_NAME_BYTES = 255
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def validate_name(name: str) -> str:
    """A single file or folder name typed in the UI."""
    if not name or not name.strip():
        raise UnsafePath("enter a name")
    if "/" in name:
        raise UnsafePath("a name can't contain '/'")
    if name in (".", ".."):
        raise UnsafePath("that name is not allowed")
    if _CONTROL.search(name):
        raise UnsafePath("a name can't contain control characters")
    if name != name.strip():
        raise UnsafePath("a name can't start or end with a space")
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise UnsafePath("that name is too long")
    if is_hidden_name(name):
        raise UnsafePath("that name is used by DriveSync itself")
    return name


def split_name(name: str) -> tuple[str, str]:
    """("report", ".pdf"); a leading-dot name like ".env" has no extension."""
    p = PurePosixPath(name)
    if p.suffix and p.stem:
        return p.stem, p.suffix
    return name, ""


def free_name(folder: Path, name: str) -> str:
    """name, or "name (1).ext", "name (2).ext", ... whichever does not exist yet."""
    if not (folder / name).exists() and not (folder / name).is_symlink():
        return name
    stem, suffix = split_name(name)
    n = 1
    while True:
        candidate = f"{stem} ({n}){suffix}"
        if not (folder / candidate).exists() and not (folder / candidate).is_symlink():
            return candidate
        n += 1
