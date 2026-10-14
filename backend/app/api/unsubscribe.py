"""Unsubscribe from mailing lists (0.5.2 §3): the sender list and sender statuses."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.mail_reader import account_or_404
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
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
    for address in body.addresses:
        set_status(db, acc.id, address, body.status, now=now)
    return {"changed": len(body.addresses)}
