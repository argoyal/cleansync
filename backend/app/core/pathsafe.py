from pathlib import Path, PurePosixPath


class UnsafePath(ValueError):
    pass


def normalize_rel(rel: str) -> str:
    if "\x00" in rel:
        raise UnsafePath("path contains a NUL byte")
    # Only '/' separates path parts. A backslash is an ordinary filename
    # character on Linux, so "..\\x" is one legal name, not an escape.
    parts = [p for p in PurePosixPath("/" + rel).parts[1:] if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UnsafePath("path may not contain '..'")
    return "/".join(parts)


def validate_remote_path(path: str) -> str:
    return normalize_rel(path)


def _resolve(path: Path) -> Path:
    # strict=True makes a symlink loop raise (Python 3.13's strict=False does not).
    # A plain missing tail is allowed, so fall back to non-strict for it.
    try:
        return path.resolve(strict=True)
    except FileNotFoundError:
        return path.resolve(strict=False)


def safe_join(base: Path, rel: str) -> Path:
    clean = normalize_rel(rel)
    candidate = base / clean if clean else base
    try:
        base_real = _resolve(base)
        real = _resolve(candidate)
    except (RuntimeError, OSError) as exc:  # e.g. a symlink loop
        raise UnsafePath("path cannot be resolved") from exc
    if real != base_real and base_real not in real.parents:
        raise UnsafePath("path leaves the allowed folder")
    return candidate
