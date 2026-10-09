"""Spec §6.9: tie the database to its disk, so a missing disk stops all syncing.

The ID lives in two places: the file /drivesync/.drivesync-volume and the settings table.
If the disk drops out, the file is gone (or a different disk is there) and nothing syncs,
so Google Drive never sees an empty folder as "everything was deleted".
"""

import logging
import os
import re
import secrets
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import SettingRow
from app.paths import Layout
from app.services.queue import MISSING_DISK_MSG

log = logging.getLogger(__name__)
VOLUME_KEY = "volume_id"
_ID = re.compile(r"[0-9a-f]{32}")
# One bad read can be a short hiccup (a virus scan, a slow network mount): read again after
# this pause before stopping all syncing until a restart.
RETRY_PAUSE_S = 1.0


def read_volume_file(path: Path) -> str | None:
    """The ID in the file, or None when it is missing, unreadable or not an ID."""
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    return text if _ID.fullmatch(text) else None


def _write_volume_file(path: Path, volume_id: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="ascii") as f:
        f.write(volume_id + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class VolumeGuard:
    def __init__(self, path: Path, expected: str | None) -> None:
        self.path = path
        self.expected = expected

    def check(self) -> str | None:
        """None while the right disk is there; the stop message otherwise."""
        if self.expected is not None:
            if read_volume_file(self.path) == self.expected:
                return None
            time.sleep(RETRY_PAUSE_S)  # runs in a worker thread, never on the event loop
            if read_volume_file(self.path) == self.expected:
                log.warning("%s could not be read once; it was fine a moment later", self.path)
                return None
        log.error(
            "%s is missing or does not hold the expected ID %s",
            self.path,
            self.expected or "(none could be written)",
        )
        return MISSING_DISK_MSG


def open_volume(db: Session, layout: Layout) -> VolumeGuard:
    path = layout.volume_file
    row = db.get(SettingRow, VOLUME_KEY)
    if row is not None:
        # The database knows its disk: never write the file, only compare.
        return VolumeGuard(path, str(row.value))
    on_disk = read_volume_file(path)
    if on_disk is None:
        on_disk = secrets.token_hex(16)
        try:
            _write_volume_file(path, on_disk)
        except OSError as e:
            log.error("could not write %s: %s; syncing stays off", path, e)
            return VolumeGuard(path, None)
        log.info("first start on this disk: wrote %s", path)
    else:
        log.info("adopting the disk ID already in %s", path)
    db.add(SettingRow(key=VOLUME_KEY, value=on_disk))
    db.commit()
    return VolumeGuard(path, on_disk)
