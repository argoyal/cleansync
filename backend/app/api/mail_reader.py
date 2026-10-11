"""The mail reader (spec §6): the list, counts, opening a mail, attachments, the .eml."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.models import Account
from app.schemas.mail import MailCountsOut, ThreadOut, ThreadPageOut
from app.services.mail.listing import label_counts, list_threads, totals, view_counts
from app.services.mail.search import SearchError, mail_conditions

MAX_QUERY_CHARS = 1000

router = APIRouter(prefix="/api/mail")


def account_or_404(db: Session, account_id: int) -> Account:
    acc = db.get(Account, account_id)
    if acc is None:
        raise HTTPException(404, "account not found")
    return acc


@router.get("/threads", response_model=ThreadPageOut)
def threads(
    account_id: int,
    view: str = "all",
    label: str | None = None,
    q: str = "",
    cursor: str | None = None,
    limit: int = 50,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account_or_404(db, account_id)
    if len(q) > MAX_QUERY_CHARS:
        raise HTTPException(
            400, f"That search is too long. Keep it under {MAX_QUERY_CHARS:,} characters."
        )
    cursor = cursor or None
    try:
        conds = mail_conditions(db, account_id, view=view, label=label or None, q=q, now=utcnow())
        rows, nxt = list_threads(db, conds, cursor=cursor, limit=max(1, min(limit, 200)))
    except SearchError as e:
        raise HTTPException(422, str(e)) from e
    total, size = totals(db, conds) if not cursor else (None, None)
    return ThreadPageOut(
        threads=[ThreadOut(**asdict(r)) for r in rows],
        next_cursor=nxt,
        total=total,
        total_bytes=size,
    )


@router.get("/counts", response_model=MailCountsOut)
def counts(
    account_id: int, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)
):
    account_or_404(db, account_id)
    return MailCountsOut(views=view_counts(db, account_id), labels=label_counts(db, account_id))
