"""A folder as a zip download, streamed: stored (no compression), never held in memory."""

import logging
import os
import zipfile
from collections.abc import Iterator
from pathlib import Path

from app.services.files.scope import is_hidden_name

log = logging.getLogger(__name__)
CHUNK = 1024 * 1024
ZIP64_ABOVE = 2 * 1024**3  # a file this big may grow past 4 GiB while we read it


class _Sink:
    """A write-only, non-seekable file: zipfile writes into it, we hand the bytes on."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._pos = 0

    def write(self, data: bytes) -> int:
        self._buf += data
        self._pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self._pos

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out = bytes(self._buf)
        self._buf.clear()
        return out


def _walk(folder: Path, arc_root: str) -> Iterator[tuple[Path, str]]:
    """(path, name in the zip) for every file and empty folder; skips links and DriveSync files."""
    stack = [(folder, arc_root)]
    while stack:
        current, arc = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = sorted(
                    (e for e in it if not is_hidden_name(e.name) and not e.is_symlink()),
                    key=lambda e: e.name,
                )
        except OSError:
            log.warning("skipped an unreadable folder in a zip download")
            continue
        if not entries:
            yield current, arc + "/"
        for e in reversed(entries):
            if e.is_dir(follow_symlinks=False):
                stack.append((Path(e.path), f"{arc}/{e.name}"))
        for e in entries:
            if e.is_file(follow_symlinks=False):
                yield Path(e.path), f"{arc}/{e.name}"


def zip_folder(folder: Path, arc_root: str) -> Iterator[bytes]:
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for path, arc in _walk(folder, arc_root):
            if arc.endswith("/"):
                zf.writestr(zipfile.ZipInfo.from_file(path, arc, strict_timestamps=False), b"")
                yield sink.take()
                continue
            try:
                info = zipfile.ZipInfo.from_file(path, arc, strict_timestamps=False)
                src = open(path, "rb")  # noqa: SIM115 - closed below
            except OSError:
                log.warning("skipped an unreadable file in a zip download")
                continue
            big = info.file_size > ZIP64_ABOVE
            try:
                with src, zf.open(info, "w", force_zip64=big) as dst:
                    while chunk := src.read(CHUNK):
                        dst.write(chunk)
                        yield sink.take()
            except OSError:
                log.warning("a file could not be read while zipping; aborting the download")
                raise
            yield sink.take()
    yield sink.take()
