"""The one-off header scan (0.5.2 §3.1): mail backed up before 0.5.2 gets its unsubscribe
headers and sender, read from the saved .eml's headers only. A mail-queue action, so it
never runs beside a backup or offload of the mailbox; in short runs, so it never holds the
queue for long. Resumes by itself: unscanned rows are exactly headers_scanned = 0."""

import asyncio
import logging
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

from sqlalchemy import func, select, update

from app.models import Account, MailMessage
from app.models.enums import ServiceKind, ServiceState
from app.services.accounts.services import service_state
from app.services.mail.service import MailService
from app.services.mail.store import eml_path, header_text, sender_of, unsubscribe_headers

log = logging.getLogger(__name__)
CHUNK = 500  # rows read and committed together
RUN_LIMIT = 5000  # rows per queue run; then it asks for itself again
HEAD_BYTES = 256 * 1024


def read_headers(path: Path) -> tuple[str | None, str | None, str | None] | None:
    """(List-Unsubscribe, List-Unsubscribe-Post, sender) from the file's header block; None
    when the file can't be read."""
    try:
        with path.open("rb") as f:
            head = f.read(HEAD_BYTES)
    except OSError:
        return None
    for sep in (b"\r\n\r\n", b"\n\n"):
        i = head.find(sep)
        if i >= 0:
            head = head[: i + len(sep)]
            break
    try:
        msg = BytesParser(policy=default).parsebytes(head, headersonly=True)
    except Exception:
        return (None, None, None)
    lu, lup = unsubscribe_headers(msg)
    return lu, lup, sender_of(header_text(msg, "From"))


def scan_progress(db, account_id: int) -> tuple[int, int]:
    total = db.scalar(
        select(func.count()).select_from(MailMessage).where(MailMessage.account_id == account_id)
    )
    todo = db.scalar(
        select(func.count())
        .select_from(MailMessage)
        .where(MailMessage.account_id == account_id, MailMessage.headers_scanned.is_(False))
    )
    return (total or 0) - (todo or 0), total or 0


class HeaderScan:
    def __init__(self, mail: MailService) -> None:
        self.mail = mail
        self.sf = mail.sf
        self.layout = mail.layout
        mail.actions["header_scan"] = self._run
        mail.tick_hooks.append(self.ask_when_needed)

    def ask_when_needed(self) -> None:
        with self.sf() as db:
            ids = list(
                db.scalars(
                    select(MailMessage.account_id)
                    .where(MailMessage.headers_scanned.is_(False))
                    .distinct()
                )
            )
            ready = [
                a for a in ids if service_state(db, a, ServiceKind.MAIL) == ServiceState.ACTIVE
            ]
        for account_id in ready:
            if not self.mail.queue.is_busy(account_id):
                self.mail.queue.request(account_id, "header_scan", {})

    async def _run(self, account_id: int, args: dict, trigger: str) -> None:
        with self.sf() as db:
            acc = db.get(Account, account_id)
            if acc is None:
                return
            email = acc.email
        done = 0
        while done < RUN_LIMIT:
            # A missing disk is not every mail's fault: stop, mark nothing (Review Focus 1).
            if await self.mail.stopped() or not await asyncio.to_thread(
                self.layout.mail_root.is_dir
            ):
                return
            with self.sf() as db:
                rows = db.execute(
                    select(MailMessage.id, MailMessage.file_path)
                    .where(
                        MailMessage.account_id == account_id,
                        MailMessage.headers_scanned.is_(False),
                    )
                    .order_by(MailMessage.id)
                    .limit(CHUNK)
                ).all()
            if not rows:
                return

            def read_all(batch=rows):
                return [
                    (rid, read_headers(eml_path(self.layout, email, rel))) for rid, rel in batch
                ]

            found = await asyncio.to_thread(read_all)
            with self.sf() as db:
                for rid, got in found:
                    lu, lup, sender = got if got is not None else (None, None, None)
                    db.execute(
                        update(MailMessage)
                        .where(MailMessage.id == rid)
                        .values(
                            list_unsubscribe=lu,
                            list_unsubscribe_post=lup,
                            sender=sender,
                            headers_scanned=True,
                        )
                    )
                db.commit()
            done += len(rows)
        self.mail.queue.request(account_id, "header_scan", {})  # more to do: queue again
