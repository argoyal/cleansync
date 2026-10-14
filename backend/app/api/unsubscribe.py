"""Unsubscribe from mailing lists (0.5.2 §3): the sender list and sender statuses."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.mail_reader import account_or_404
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.models import UnsubscribeSender
from app.models.enums import UnsubscribeMethod
from app.schemas.unsubscribe import ChangedOut, MarkIn, SendersOut
from app.services.mail.header_scan import scan_progress
from app.services.unsubscribe.senders import (
    SORTS,
    TABS,
    list_senders,
    set_status,
    suggested_count,
)

router = APIRouter(prefix="/api/mail/unsubscribe")


@router.get("/senders", response_model=SendersOut)
def senders(
    account_id: int,
    tab: str = "suggested",
    q: str = "",
    sort: str = "mails",
    page: int = 1,
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_user),
):
    acc = account_or_404(db, account_id)
    if tab not in TABS:
        raise HTTPException(400, "unknown tab")
    if sort not in SORTS:
        raise HTTPException(400, "unknown sort")
    items, total = list_senders(db, acc.id, acc.email, tab=tab, q=q, sort=sort, page=page)
    scanned, scan_total = scan_progress(db, acc.id)
    return {
        "items": [asdict(r) for r in items],
        "total": total,
        "suggested": suggested_count(db, acc.id, acc.email),
        "scan": {"scanned": scanned, "total": scan_total},
    }


@router.post("/mark", response_model=ChangedOut)
def mark(
    body: MarkIn,
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_user),
):
    """Mark senders unsubscribed (by hand, outside CleanSync), or clear their status."""
    acc = account_or_404(db, body.account_id)
    now = utcnow()
    method = UnsubscribeMethod.MANUAL.value if body.status else None
    changed = 0
    for address in dict.fromkeys(a.strip().lower() for a in body.addresses if a.strip()):
        row = db.scalar(
            select(UnsubscribeSender).where(
                UnsubscribeSender.account_id == acc.id, UnsubscribeSender.address == address
            )
        )
        if (row.status if row else None) == body.status:
            continue  # nothing to change; clearing a sender with no row makes no row
        set_status(db, acc.id, address, body.status, method=method, now=now, commit=False)
        changed += 1
    db.commit()
    return {"changed": changed}
