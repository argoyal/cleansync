from pathlib import Path, PurePosixPath


class UnsafePath(ValueError):
    pass


def normalize_rel(rel: str) -> str:
    if "\x00" in rel:
        raise UnsafePath("path contains a NUL byte")
    parts = [p for p in PurePosixPath("/" + rel).parts[1:] if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UnsafePath("path may not contain '..'")
    return "/".join(parts)


def validate_remote_path(path: str) -> str:
    return normalize_rel(path)


def safe_join(base: Path, rel: str) -> Path:
    clean = normalize_rel(rel)
    candidate = base / clean if clean else base
    base_real = base.resolve()
    real = candidate.resolve(strict=False)
    if real != base_real and base_real not in real.parents:
        raise UnsafePath("path leaves the allowed folder")
    return candidate
