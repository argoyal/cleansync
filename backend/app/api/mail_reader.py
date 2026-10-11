"""The mail reader (spec §6): the list, counts, opening a mail, attachments, the .eml."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.models import Account, MailMessage
from app.schemas.mail import MailCountsOut, ThreadOut, ThreadPageOut
from app.services.mail.listing import label_counts, list_threads, totals, view_counts
from app.services.mail.reader import MailFileMissing, body_csp, read_eml_of, render_body
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


def message_or_404(db: Session, message_id: int) -> tuple[MailMessage, Account]:
    row = db.get(MailMessage, message_id)
    if row is None:
        raise HTTPException(404, "mail not found")
    return row, account_or_404(db, row.account_id)


def eml_bytes(request: Request, account: Account, row: MailMessage) -> bytes:
    try:
        return read_eml_of(request.app.state.layout, account.email, row)
    except MailFileMissing as e:
        raise HTTPException(404, str(e)) from e


@router.get("/messages/{message_id}/body")
def body(
    message_id: int,
    request: Request,
    images: bool = False,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    """A plain (not async) route, so reading and cleaning a big mail runs in a worker thread
    and never holds up the event loop."""
    row, account = message_or_404(db, message_id)
    page = render_body(eml_bytes(request, account, row))
    return Response(
        page.html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": body_csp(images),
            "X-Frame-Options": "SAMEORIGIN",
            "Cache-Control": "private, no-store",
        },
    )
