"""Uploads stream to a temp file under app/data/uploads, then move into place in one step.

The temp file is outside every synced folder, so neither rclone nor the watcher ever sees
a half-written file. app/data and storage/ share one disk, so the final move is a rename.
"""

import asyncio
import errno
import os
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from app.core.pathsafe import safe_join
from app.paths import Layout
from app.services.files.names import free_name, validate_name
from app.services.files.ops import FileOpError
from app.services.files.scope import (
    UPLOAD_TEMP_PREFIX,
    Scope,
    is_hidden,
    join_rel,
    resolves_to_hidden,
)

FLUSH_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 64


def plan_target(
    scope: Scope,
    folder_rel: str,
    relative_name: str,
) -> tuple[Path, str, str]:
    """(folder on disk, its account path, file name); creates sub-folders of a folder upload."""
    parts = relative_name.split("/")
    if len(parts) > MAX_DEPTH:
        raise FileOpError("that folder is nested too deeply", 422)
    parts = [validate_name(p) for p in parts]
    if is_hidden(folder_rel):
        raise FileOpError("folder not found", 404)
    folder = safe_join(scope.root, folder_rel)
    if not folder.is_dir() or resolves_to_hidden(scope.root, folder):
        raise FileOpError("folder not found", 404)
    dir_rel = folder_rel
    for part in parts[:-1]:
        dir_rel = join_rel(dir_rel, part)
        d = safe_join(scope.root, dir_rel)
        if d.is_symlink() or (d.exists() and not d.is_dir()):
            raise FileOpError(f"'{part}' is a file, not a folder", 409)
        d.mkdir(exist_ok=True)
        if resolves_to_hidden(scope.root, d):
            raise FileOpError("folder not found", 404)
    return safe_join(scope.root, dir_rel), dir_rel, parts[-1]


def new_temp(layout: Layout) -> Path:
    return layout.uploads_dir / f"{uuid.uuid4().hex}.part"


def _flush(fh) -> None:
    fh.flush()
    os.fsync(fh.fileno())


async def save_stream(stream: AsyncIterator[bytes], tmp: Path) -> int:
    """Write the request body to tmp in large pieces, off the event loop. Returns its size."""
    total = 0
    buf = bytearray()
    fh = await asyncio.to_thread(open, tmp, "xb")
    try:
        async for chunk in stream:
            buf += chunk
            total += len(chunk)
            if len(buf) >= FLUSH_BYTES:
                data = bytes(buf)
                buf.clear()
                await asyncio.to_thread(fh.write, data)
        if buf:
            await asyncio.to_thread(fh.write, bytes(buf))
        await asyncio.to_thread(_flush, fh)
    finally:
        await asyncio.to_thread(fh.close)
    return total


def _copy_beside(tmp: Path, folder: Path) -> Path:
    """Copy tmp into the destination folder under a short hidden name (other disk)."""
    hidden = folder / f"{UPLOAD_TEMP_PREFIX}{uuid.uuid4().hex}"
    try:
        shutil.copyfile(tmp, hidden)
        shutil.copystat(tmp, hidden)
    except BaseException:
        hidden.unlink(missing_ok=True)
        raise
    return hidden


def _place(source: Path, dest: Path) -> None:
    """Put source at dest without ever replacing something already there.

    Raises FileExistsError when dest is taken, and OSError(EXDEV) when source is on another
    disk. Hard link first (atomic); without hard links (some Windows mounts) reserve the name
    with an exclusive create, then replace only that empty placeholder."""
    try:
        os.link(source, dest)
        return
    except FileExistsError:
        raise
    except OSError as e:
        if e.errno == errno.EXDEV:
            raise
    os.close(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))  # we own the name now
    try:
        os.replace(source, dest)
    except BaseException:
        try:
            if dest.stat().st_size == 0:  # still our placeholder, not someone's real file
                dest.unlink()
        except OSError:
            pass
        raise


def publish(tmp: Path, folder: Path, name: str) -> str:
    """Move the finished upload into the folder under a free name; never overwrites."""
    hidden: Path | None = None
    try:
        for _ in range(1000):
            final = free_name(folder, name)
            dest = folder / final
            try:
                _place(hidden or tmp, dest)
            except FileExistsError:
                continue
            except OSError as e:
                if e.errno != errno.EXDEV or hidden is not None:
                    raise
                # storage/ is on another disk than app/data: copy beside the target first.
                hidden = _copy_beside(tmp, folder)
                continue
            tmp.unlink(missing_ok=True)
            return final
        raise FileOpError("could not find a free name for the upload", 409)
    finally:
        if hidden is not None:
            hidden.unlink(missing_ok=True)


def clear_uploads(layout: Layout) -> int:
    """At startup no upload can be in progress: remove what crashed uploads left behind."""
    removed = 0
    for f in layout.uploads_dir.glob("*.part"):
        f.unlink(missing_ok=True)
        removed += 1
    return removed


STALE_TEMP_S = 3600.0


def sweep_upload_leftovers(
    storage: Path, max_age_s: float = STALE_TEMP_S, now: float | None = None
) -> int:
    """Remove upload copies a crash left inside synced folders. Only ones older than
    max_age_s, so a copy an upload is placing right now is never touched: the copy keeps the
    uploaded file's own modified time, so its inode change time (made now) counts too.
    Never follows links."""
    cutoff = (time.time() if now is None else now) - max_age_s
    removed = 0
    for folder, _dirs, files in os.walk(storage, followlinks=False):
        for name in files:
            if not name.casefold().startswith(UPLOAD_TEMP_PREFIX):
                continue
            path = Path(folder) / name
            try:
                st = path.lstat()
                if max(st.st_mtime, st.st_ctime) < cutoff and not path.is_symlink():
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    return removed
