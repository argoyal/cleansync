"""Mail offload: what one would do (the preview, spec 5.2), and tidying after a stop."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import MailOffload
from app.models.enums import MailOffloadState

GMAIL_TRASH_DAYS = 30  # Gmail empties its trash after this many days (spec 5.4)
INTERRUPTED_OFFLOAD_MSG = (
    "CleanSync stopped during this offload. Mails already offloaded stay offloaded; "
    "the rest stayed in Gmail."
)


def recover_mail_offloads(db: Session) -> int:
    """At startup: an offload a stop cut short ends as FAILED, with the reason.

    Only RUNNING ones. A WAITING offload (not started yet, or paused by the budget until
    the next UTC day) keeps waiting.
    """
    rows = list(
        db.scalars(select(MailOffload).where(MailOffload.state == MailOffloadState.RUNNING))
    )
    for off in rows:
        off.state = MailOffloadState.FAILED
        off.reason = INTERRUPTED_OFFLOAD_MSG
        off.finished_at = utcnow()
    db.commit()
    return len(rows)
