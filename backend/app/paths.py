import re
from dataclasses import dataclass
from pathlib import Path

_EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$")


def email_dirname(email: str) -> str:
    name = email.strip().lower()
    if not _EMAIL_RE.match(name):
        raise ValueError(f"not a usable email for a folder name: {email!r}")
    return name


@dataclass(frozen=True)
class Layout:
    root: Path

    @property
    def data(self) -> Path:
        return self.root / "app" / "data"

    @property
    def db_file(self) -> Path:
        return self.data / "drivesync.db"

    @property
    def db_backups(self) -> Path:
        return self.data / "db-backups"

    @property
    def rclone_dir(self) -> Path:
        return self.data / "rclone"

    @property
    def rclone_conf(self) -> Path:
        return self.rclone_dir / "rclone.conf"

    @property
    def rclone_cache(self) -> Path:
        return self.rclone_dir / "cache"

    @property
    def bisync_dir(self) -> Path:
        return self.rclone_dir / "bisync"

    @property
    def filters_dir(self) -> Path:
        return self.data / "filters"

    @property
    def catalog_dir(self) -> Path:
        return self.data / "catalog"

    @property
    def previews_dir(self) -> Path:
        return self.data / "previews"

    @property
    def backups_dir(self) -> Path:
        return self.data / "backups"

    @property
    def thumbs_dir(self) -> Path:
        return self.data / "thumbs"

    @property
    def uploads_dir(self) -> Path:
        # Same disk as storage/, so a finished upload is moved into place, not copied.
        return self.data / "uploads"

    @property
    def offloads_dir(self) -> Path:
        # One JSON file per offload: each file's path, size, MD5 and Drive ID at offload time.
        return self.data / "offloads"

    @property
    def logs_dir(self) -> Path:
        return self.data / "logs"

    @property
    def jobs_log_dir(self) -> Path:
        return self.logs_dir / "jobs"

    @property
    def secrets_dir(self) -> Path:
        return self.data / "secrets"

    @property
    def storage(self) -> Path:
        return self.root / "storage"

    @property
    def mail_root(self) -> Path:
        # Spec §3.3: next to storage/, so mail is never inside a Drive sync pair's folder
        # (and never uploaded to Drive).
        return self.root / "mail"

    def mail_dir(self, email: str) -> Path:
        return self.mail_root / email_dirname(email)

    def mail_job_log(self, job_id: int) -> Path:
        return self.jobs_log_dir / f"mail-{job_id}.log"

    @property
    def volume_file(self) -> Path:
        # Spec §6.9: marks the right disk; checked at startup and before every job.
        return self.root / ".drivesync-volume"

    def ensure(self) -> None:
        for d in (
            self.db_backups,
            self.rclone_dir,
            self.rclone_cache,
            self.bisync_dir,
            self.filters_dir,
            self.catalog_dir,
            self.previews_dir,
            self.backups_dir,
            self.thumbs_dir,
            self.uploads_dir,
            self.offloads_dir,
            self.jobs_log_dir,
            self.storage,
            self.mail_root,
        ):
            d.mkdir(parents=True, exist_ok=True)
        self.secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.secrets_dir.chmod(0o700)

    def account_dir(self, email: str) -> Path:
        return self.storage / email_dirname(email)

    def pair_workdir(self, pair_id: int) -> Path:
        return self.bisync_dir / str(pair_id)

    def pair_filters(self, pair_id: int) -> Path:
        return self.filters_dir / f"{pair_id}.txt"

    def job_log(self, job_id: int) -> Path:
        return self.jobs_log_dir / f"{job_id}.log"
