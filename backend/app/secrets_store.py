import os
import secrets
from pathlib import Path


def _read_saved(file: Path) -> str:
    return file.read_text().strip()


def resolve_secret(value: str, file: Path) -> str:
    """Use the env value if given; else reuse the saved one; else generate and save it (mode 600).

    The saved file is published atomically: the value is written to a private temp file, fsynced,
    then hard-linked into place. A visible secret file is therefore always complete. If another
    process publishes first, its value wins. An empty leftover file is treated as missing.
    """
    if value:
        return value
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    while True:
        if file.exists():
            saved = _read_saved(file)
            if saved:
                return saved
            file.unlink(missing_ok=True)  # empty leftover from an interrupted run

        generated = secrets.token_urlsafe(48)
        tmp = file.with_name(f".{file.name}.{secrets.token_hex(8)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(generated)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(tmp, file)
            except FileExistsError:
                continue  # another process published first; read theirs
            except OSError:
                # Some filesystems (Windows drive mounts, some Docker binds) refuse hard links.
                # Another process may have published meanwhile: prefer its value.
                if file.exists():
                    saved = _read_saved(file)
                    if saved:
                        return saved
                os.replace(tmp, file)
            return generated
        finally:
            tmp.unlink(missing_ok=True)
