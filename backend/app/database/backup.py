import sqlite3
from datetime import date
from pathlib import Path


def backup_database(db_file: Path, backups_dir: Path, today: date, keep: int = 14) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    target = backups_dir / f"drivesync-{today.isoformat()}.db"
    src = sqlite3.connect(db_file)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    existing = sorted(backups_dir.glob("drivesync-*.db"))
    for old in existing[:-keep]:
        old.unlink()
    return target
