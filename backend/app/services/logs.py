import logging
import os
from pathlib import Path

from app.paths import Layout

MAX_SCAN_BYTES = 8 * 1024 * 1024
MAX_LINE_CHARS = 10_000

log = logging.getLogger(__name__)


def tail_lines(path: Path, n: int) -> list[str]:
    if n <= 0:
        return []
    try:
        fh = open(path, "rb")  # noqa: SIM115 - closed below; missing file is a normal case
    except FileNotFoundError:
        return []
    with fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        scanned = 0
        newlines = 0
        data = b""
        while pos > 0 and newlines <= n and scanned < MAX_SCAN_BYTES:
            step = min(65536, pos)
            pos -= step
            scanned += step
            fh.seek(pos)
            chunk = fh.read(step)
            newlines += chunk.count(b"\n")
            data = chunk + data
    lines = data.decode("utf-8", "replace").splitlines()[-n:]
    return [ln[:MAX_LINE_CHARS] for ln in lines]


def prune_job_logs(layout: Layout, older_than_days: int, now: float) -> int:
    cutoff = now - older_than_days * 86400
    removed = 0
    for f in layout.jobs_log_dir.glob("*.log"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            log.exception("could not prune job log %s", f)
    return removed
