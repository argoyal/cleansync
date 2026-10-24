"""Add a pretend Google account whose "Drive" is a local folder (an rclone alias remote).

For development (`make dev-seed`) and the browser smoke test in CI: no Google login needed.
Drive-only features (Open in Google, new Google Docs) do not work for such an account.
Needs DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 so it can never happen by accident in production.

    python -m app.devtools.seed_local_account alice@example.com [--folder DIR] [--sample-files] [--sample-mail]

--sample-mail gives it a fake Gmail mailbox (app/data/fake-gmail/<email>.json) that its mail backup reads.
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
from app.services.accounts.service import AccountError, AccountService, check_local_folder
from app.services.events.bus import EventBus
from app.services.rclone.commands import UnsafeArgument
from app.services.rclone.setup import build_rclone_env


def write_samples(folder: Path) -> None:
    from PIL import Image

    (folder / "Welcome.txt").write_text("This folder pretends to be a Google Drive.\n")
    (folder / "Photos").mkdir(exist_ok=True)
    Image.new("RGB", (640, 400), (70, 110, 160)).save(folder / "Photos" / "sky.png")
    (folder / "Reports").mkdir(exist_ok=True)
    Image.new("RGB", (595, 842), "white").save(folder / "Reports" / "summary.pdf")


def write_sample_mail(layout: Layout, email: str, unsubscribe: bool = False) -> Path:
    """The pretend account's Gmail: the sample mailbox (or, for the Unsubscribe browser test,
    the small unsubscribe one), saved where its mail adapter reads it."""
    from app.core.clock import utcnow
    from app.devtools.fake_gmail import FakeGmail, sample_mailbox, unsubscribe_mailbox

    fake = FakeGmail(email=email)
    (unsubscribe_mailbox if unsubscribe else sample_mailbox)(fake, utcnow())
    fake.path = layout.data / "fake-gmail" / f"{email}.json"
    fake.save()  # once, at the end: each change would rewrite the whole file
    return fake.path


async def seed(
    settings: Settings,
    email: str,
    folder: Path | None,
    samples: bool,
    mail: bool = False,
    unsub_mail: bool = False,
) -> str:
    if not settings.allow_local_accounts:
        raise SystemExit(
            "Refusing: pretend accounts are for development only. "
            "Set DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 to allow one."
        )
    layout = Layout(settings.drivesync_root)
    layout.ensure()
    run_migrations(layout.db_file, wal=settings.database_wal)
    email = email_dirname(email)
    env = build_rclone_env(settings, layout)
    try:
        target = check_local_folder(layout, env, folder or layout.root / "local-remotes" / email)
    except (AccountError, UnsafeArgument) as e:
        raise SystemExit(str(e)) from e
    if samples:  # before the account exists, so a failure here leaves no half-made account
        target.mkdir(parents=True, exist_ok=True)
        write_samples(target)
    engine = make_engine(layout.db_file, wal=settings.database_wal)
    try:
        service = AccountService(
            settings=settings,
            layout=layout,
            session_factory=sessionmaker(engine, expire_on_commit=False),
            bus=EventBus(),
            rclone_env=env,
        )
        try:
            await service.add_local_account(email, target)
        except (AccountError, UnsafeArgument) as e:
            raise SystemExit(str(e)) from e
        if mail or unsub_mail:
            write_sample_mail(layout, email, unsubscribe=unsub_mail)
    finally:
        engine.dispose()
    text = (
        f"Added {email}. Its pretend Google Drive is {target}\n"
        "If the app is running, restart the app to pick up the new account."
    )
    if mail or unsub_mail:
        text += "\nIts pretend Gmail has sample mail; set up mail backup in the Mail section."
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    parser.add_argument("--folder", type=Path, help="the local folder that acts as Drive")
    parser.add_argument("--sample-files", action="store_true", help="put a few files in it")
    parser.add_argument(
        "--sample-mail", action="store_true", help="give it a fake Gmail with sample mail"
    )
    parser.add_argument(
        "--unsub-mail",
        action="store_true",
        help="give it a fake Gmail with two senders to unsubscribe from (the browser test)",
    )
    args = parser.parse_args(argv)
    print(
        asyncio.run(
            seed(
                Settings(),
                args.email,
                args.folder,
                args.sample_files,
                args.sample_mail,
                args.unsub_mail,
            )
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
