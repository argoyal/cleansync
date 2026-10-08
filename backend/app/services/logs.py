import logging
import os
from pathlib import Path

from app.paths import Layout

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
        data = b""
        while pos > 0 and data.count(b"\n") <= n:
            step = min(65536, pos)
            pos -= step
            fh.seek(pos)
            data = fh.read(step) + data
    return data.decode("utf-8", "replace").splitlines()[-n:]


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
