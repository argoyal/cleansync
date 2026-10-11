"""Mail offload and restore (spec 5): out of Gmail by filter or by pick, into Gmail's trash
(checked before and after), and back into Gmail without ever making a second copy.
Runs as mail queue actions, so never beside a backup of the account."""

import asyncio
import logging

from app.models import Account
from app.models.enums import MailPhase
from app.services.app_settings import get_setting
from app.services.mail.index import sync_state
from app.services.mail.offload_plan import OffloadPreview, Pick, plan_offload
from app.services.mail.search import SearchError
from app.services.mail.service import MailError, MailService

log = logging.getLogger(__name__)
BACKUP_FIRST_MSG = (
    "The first mail backup hasn't finished yet. Offload works once every mail is backed up."
)
BACKUP_CHECKING_MSG = (
    "Wait until the mail backup has finished checking your mailbox, then try again."
)


class MailOffloads:
    def __init__(self, mail: MailService) -> None:
        self.mail = mail
        self.sf = mail.sf
        self.layout = mail.layout
        self.bus = mail.bus
        self.clock = mail.clock

    def _min_age(self, db) -> int:
        return int(get_setting(db, "mail_min_age_days"))  # type: ignore[arg-type]

    async def _ready(self, account_id: int) -> Account:
        """Spec 5.2 refusals: the disk, the login, Mail set up, and a backup that is not
        still listing the whole mailbox (the first one, or a recheck)."""
        acc = await self.mail.ready(account_id)
        with self.sf() as db:
            phase = sync_state(db, account_id).phase
        if phase == MailPhase.INITIAL:
            raise MailError(BACKUP_FIRST_MSG)
        if phase == MailPhase.RECHECK:
            raise MailError(BACKUP_CHECKING_MSG)
        return acc

    async def preview(self, account_id: int, pick: Pick) -> OffloadPreview:
        acc = await self._ready(account_id)

        def work() -> OffloadPreview:
            with self.sf() as db:
                return plan_offload(
                    db, self.layout, acc, pick, min_age_days=self._min_age(db), now=self.clock()
                )

        try:
            return await asyncio.to_thread(work)  # stats every file: off the event loop
        except SearchError as e:
            raise MailError(str(e), 422) from e
