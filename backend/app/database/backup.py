import os
import sqlite3
from datetime import date
from pathlib import Path


def backup_database(db_file: Path, backups_dir: Path, today: date, keep: int = 14) -> Path:
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValueError(f"keep must be an int >= 1: {keep!r}")
    backups_dir.mkdir(parents=True, exist_ok=True)
    target = backups_dir / f"drivesync-{today.isoformat()}.db"
    # Write to a temporary name first: a failed backup never replaces or leaves a broken file.
    tmp = backups_dir / f".{target.name}.tmp"
    tmp.unlink(missing_ok=True)
    try:
        src = sqlite3.connect(db_file)
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    existing = sorted(backups_dir.glob("drivesync-*.db"))
    for old in existing[:-keep]:
        old.unlink()
    return target
