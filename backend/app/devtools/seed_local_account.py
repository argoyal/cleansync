"""Add a pretend Google account whose "Drive" is a local folder (an rclone alias remote).

For development (`make dev-seed`) and the browser smoke test in CI: no Google login needed.
Drive-only features (Open in Google, new Google Docs) do not work for such an account.
Needs DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 so it can never happen by accident in production.

    python -m app.devtools.seed_local_account alice@example.com [--folder DIR] [--sample-files]
"""

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database.engine import make_engine
from app.database.migrate import run_migrations
from app.paths import Layout, email_dirname
from app.services.accounts.service import AccountError, AccountService
from app.services.events.bus import EventBus
from app.services.rclone.setup import build_rclone_env


def write_samples(folder: Path) -> None:
    from PIL import Image

    (folder / "Welcome.txt").write_text("This folder pretends to be a Google Drive.\n")
    (folder / "Photos").mkdir(exist_ok=True)
    Image.new("RGB", (640, 400), (70, 110, 160)).save(folder / "Photos" / "sky.png")
    (folder / "Reports").mkdir(exist_ok=True)
    Image.new("RGB", (595, 842), "white").save(folder / "Reports" / "summary.pdf")


async def seed(settings: Settings, email: str, folder: Path | None, samples: bool) -> str:
    if not settings.allow_local_accounts:
        raise SystemExit(
            "Refusing: pretend accounts are for development only. "
            "Set DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 to allow one."
        )
    layout = Layout(settings.drivesync_root)
    layout.ensure()
    run_migrations(layout.db_file)
    email = email_dirname(email)
    target = (folder or layout.root / "local-remotes" / email).resolve()
    engine = make_engine(layout.db_file)
    try:
        service = AccountService(
            settings=settings,
            layout=layout,
            session_factory=sessionmaker(engine, expire_on_commit=False),
            bus=EventBus(),
            rclone_env=build_rclone_env(settings, layout),
        )
        try:
            await service.add_local_account(email, target)
        except AccountError as e:
            raise SystemExit(str(e)) from e
    finally:
        engine.dispose()
    if samples:
        write_samples(target)
    return f"Added {email}. Its pretend Google Drive is {target}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    parser.add_argument("--folder", type=Path, help="the local folder that acts as Drive")
    parser.add_argument("--sample-files", action="store_true", help="put a few files in it")
    args = parser.parse_args(argv)
    print(asyncio.run(seed(Settings(), args.email, args.folder, args.sample_files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
