"""Mail backup (spec §4.3–4.5). One run does what the account's phase needs: the first
backup (listing every mail, resumable), then keeping up from Gmail's history (Task 8), or a
re-check when the history is too old (Task 8); then it tries failed mails again."""

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import utcnow
from app.models.enums import JobState, MailPhase, ServiceKind, ServiceState
from app.paths import Layout
from app.services.accounts.services import set_service_state
from app.services.events.bus import EventBus
from app.services.google.client import RateLimited
from app.services.google.quota import BudgetReached
from app.services.mail.adapter import (
    SKIP_LABELS,
    MailAdapter,
    MailApiError,
    MailAuthError,
    MailNotFound,
)
from app.services.mail.index import (
    add_message,
    clear_failure,
    count_messages,
    due_failures,
    record_failure,
    save_labels,
    stored_ids,
    sync_state,
    waiting_failures,
)
from app.services.mail.jobs import job_log, update_mail_job
from app.services.mail.store import MailRootMissing, eml_rel, parse_eml, write_eml
from app.services.queue import MISSING_DISK_MSG

log = logging.getLogger(__name__)
GB = 1024**3
DISK_LOW_MSG = (
    "Paused: less than {floor} GB free on the storage disk. Free some space, then press Check now."
)
GMAIL_TROUBLE_MSG = "Gmail had a problem ({error}). It tries again at the next check."


def _disk_free(path: Path) -> int:
    return shutil.disk_usage(path).free


class Paused(Exception):
    """Stop this run for now with a reason a person can act on (disk space, missing disk)."""


@dataclass
class BackupRun:
    account_id: int
    email: str
    adapter: MailAdapter
    job_id: int
    floor_bytes: int
    is_stopped: Callable[[], Awaitable[bool]]
    log_path: str | None = None
    downloaded: int = 0
    bytes: int = 0
    failed: int = 0
    login_lost: bool = False


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


class MailBackup:
    def __init__(
        self,
        *,
        layout: Layout,
        session_factory: sessionmaker[Session],
        bus: EventBus,
        clock=utcnow,
        disk_free: Callable[[Path], int] = _disk_free,
    ) -> None:
        self.layout = layout
        self.sf = session_factory
        self.bus = bus
        self.clock = clock
        self.disk_free = disk_free
        # What each phase does. Task 8 adds keeping up and the re-check.
        self.steps: dict[str, Callable[[BackupRun], Awaitable[None]]] = {
            MailPhase.INITIAL: self._initial,
        }

    # ---- the run -------------------------------------------------------------------------
    async def run(self, r: BackupRun) -> tuple[str, str]:
        try:
            await self._labels(r)
            ran: set[str] = set()
            while True:
                with self.sf() as db:
                    phase = sync_state(db, r.account_id).phase
                step = self.steps.get(phase)
                if step is None or phase in ran:
                    break
                ran.add(phase)
                await step(r)
            await self._retry_failures(r)
        except (BudgetReached, RateLimited, Paused) as e:
            return self._end(r, JobState.PAUSED, str(e))
        except MailAuthError as e:
            r.login_lost = e.login
            with self.sf() as db:
                set_service_state(
                    db, r.account_id, ServiceKind.MAIL, ServiceState.NEEDS_RECONNECT, str(e)
                )
            return self._end(r, JobState.FAILED, str(e))
        except MailApiError as e:
            return self._end(r, JobState.FAILED, GMAIL_TROUBLE_MSG.format(error=e))
        return self._end(r, JobState.SUCCESS, None)

    def _end(self, r: BackupRun, state: str, problem: str | None) -> tuple[str, str]:
        with self.sf() as db:
            st = sync_state(db, r.account_id)
            st.last_run_at = self.clock()
            st.last_error = problem
            st.done = count_messages(db, r.account_id)
            db.commit()
            update_mail_job(db, r.job_id, done=r.downloaded, bytes_=r.bytes)
        if problem is not None:
            return state, problem
        if not r.downloaded and not r.failed:
            return state, "Nothing new"
        text = f"Saved {_plural(r.downloaded, 'new mail', 'new mails')}"
        if r.failed:
            text += f"; {r.failed:,} couldn't be saved and will be tried again"
        return state, text

    def _log(self, r: BackupRun, line: str) -> None:
        job_log(r.log_path, line)

    def _progress(self, r: BackupRun) -> None:
        with self.sf() as db:
            st = sync_state(db, r.account_id)
            st.done = count_messages(db, r.account_id)
            db.commit()
            update_mail_job(db, r.job_id, done=r.downloaded, bytes_=r.bytes)
            done, total = st.done, st.total
        self.bus.publish(
            "mail", {"account_id": r.account_id, "job_id": r.job_id, "done": done, "total": total}
        )

    async def _check_stopped(self, r: BackupRun) -> None:
        if await r.is_stopped():
            raise Paused(MISSING_DISK_MSG)

    # ---- phases --------------------------------------------------------------------------
    async def _labels(self, r: BackupRun) -> None:
        labels = await r.adapter.labels()
        with self.sf() as db:
            save_labels(db, r.account_id, labels)

    async def _list_pages(self, r: BackupRun, on_page) -> None:
        """Every page of the mail list from the saved cursor; the cursor is saved after each
        page, so a stop anywhere resumes at the next page."""
        with self.sf() as db:
            cursor = sync_state(db, r.account_id).cursor
        while True:
            await self._check_stopped(r)
            try:
                ids, nxt = await r.adapter.list_page(cursor)
            except MailApiError as e:
                if cursor and e.status == 400:
                    # Gmail no longer takes the saved page cursor: start the list again.
                    # Stored mail is skipped, so only listing calls repeat.
                    self._log(
                        r, "Gmail refused the saved place in the list; starting the list again"
                    )
                    cursor = None
                    continue
                raise
            await on_page(ids)
            with self.sf() as db:
                st = sync_state(db, r.account_id)
                st.cursor = nxt
                db.commit()
            self._progress(r)
            if nxt is None:
                return
            cursor = nxt

    async def _download_new(self, r: BackupRun, ids: list[str]) -> None:
        with self.sf() as db:
            have = stored_ids(db, r.account_id, ids)
            waiting = waiting_failures(db, r.account_id, ids, self.clock())
        for mid in ids:
            if mid not in have and mid not in waiting:
                await self._download(r, mid)

    async def _initial(self, r: BackupRun) -> None:
        with self.sf() as db:
            st = sync_state(db, r.account_id)
            need_profile = st.history_id is None
        if need_profile:
            info = await r.adapter.profile()
            with self.sf() as db:
                st = sync_state(db, r.account_id)
                # Taken before the listing starts, so mail that arrives during the backup is
                # caught by the first check afterwards (spec §4.3).
                st.history_id = info.history_id
                st.total = info.total
                db.commit()
            self._log(r, f"first backup: Gmail has about {info.total:,} mails")
        await self._list_pages(r, lambda ids: self._download_new(r, ids))
        with self.sf() as db:
            st = sync_state(db, r.account_id)
            st.phase = MailPhase.INCREMENTAL
            st.cursor = None
            db.commit()
        self._log(r, "first backup: the whole list is done; keeping up from now on")

    async def _retry_failures(self, r: BackupRun) -> None:
        with self.sf() as db:
            due = due_failures(db, r.account_id, self.clock())
            have = stored_ids(db, r.account_id, due)
        if due:
            # The disk check: an empty mount point left by a dropped disk still looks like a
            # folder, so ask the volume guard before writing anything here too.
            await self._check_stopped(r)
        for mid in due:
            if mid in have:
                with self.sf() as db:
                    clear_failure(db, r.account_id, mid)
                continue
            await self._download(r, mid)

    # ---- one mail ------------------------------------------------------------------------
    def _check_disk(self, r: BackupRun) -> None:
        try:
            free = self.disk_free(self.layout.mail_root)
        except OSError:
            raise Paused(MISSING_DISK_MSG) from None
        if free < r.floor_bytes:
            raise Paused(DISK_LOW_MSG.format(floor=round(r.floor_bytes / GB)))

    def _failed(self, r: BackupRun, mid: str, error: str) -> None:
        with self.sf() as db:
            f = record_failure(db, r.account_id, mid, error, self.clock())
        r.failed += 1
        self._log(r, f"could not save {mid} (try {f.attempts}): {error}")

    async def _download(self, r: BackupRun, mid: str) -> bool:
        self._check_disk(r)  # before anything is written (spec §4.5)
        try:
            raw, meta = await r.adapter.fetch_raw(mid)
        except MailNotFound:
            with self.sf() as db:
                clear_failure(db, r.account_id, mid)  # deleted in Gmail meanwhile
            return False
        except MailAuthError:
            raise
        except MailApiError as e:
            self._failed(r, mid, str(e))
            return False
        if SKIP_LABELS & set(meta.labels):
            with self.sf() as db:
                clear_failure(db, r.account_id, mid)  # spam, trash, a draft or a chat
            return False
        parsed = parse_eml(raw)
        try:
            rel = eml_rel(meta.id, meta.internal_date)
            size, sha = await asyncio.to_thread(write_eml, self.layout, r.email, rel, raw)
        except MailRootMissing as e:
            raise Paused(str(e)) from None
        except OSError as e:
            self._failed(r, mid, f"could not write the file: {e.strerror or e}")
            return False
        with self.sf() as db:
            # Only now that the file is complete on disk (spec §4.3).
            add_message(db, r.account_id, meta, parsed, rel, size, sha, self.clock())
            clear_failure(db, r.account_id, mid)
        r.downloaded += 1
        r.bytes += size
        self._log(r, f"saved {rel}")
        return True
