"""The mail reader (spec §6): the list, counts, opening a mail, attachments, the .eml."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serve import content_disposition, csp_for
from app.auth.deps import AuthContext, get_db, require_user
from app.core.clock import utcnow
from app.models import Account, MailMessage
from app.models.enums import MailState
from app.schemas.mail import AttachmentOut, MailCountsOut, MessageOut, ThreadOut, ThreadPageOut
from app.services.files.mimes import serve_policy
from app.services.mail.gmail import valid_mail_id
from app.services.mail.listing import label_counts, list_threads, totals, view_counts
from app.services.mail.reader import (
    MailFileMissing,
    attachment_of,
    body_csp,
    can_preview,
    gmail_url,
    has_remote_images,
    label_names,
    read_eml_of,
    render_body,
)
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


def message_out(request: Request, db: Session, row: MailMessage, account: Account) -> MessageOut:
    try:
        raw: bytes | None = read_eml_of(request.app.state.layout, account.email, row)
    except MailFileMissing:
        raw = None
    attachments = (
        [
            AttachmentOut(
                index=i,
                name=a["name"],
                type=a["type"],
                size=a["size"],
                preview=can_preview(a["type"]),
            )
            for i, a in enumerate(row.attachments or [])
        ]
        if raw is not None
        else []
    )
    return MessageOut(
        id=row.id,
        account_id=row.account_id,
        provider_id=row.provider_id,
        thread_id=row.thread_id,
        from_addr=row.from_addr,
        to_addrs=row.to_addrs,
        cc_addrs=row.cc_addrs,
        subject=row.subject,
        date=row.date,
        size=row.size,
        file_size=row.file_size,
        labels=label_names(db, row.account_id, list(row.labels or [])),
        state=row.state,
        offload_id=row.offload_id,
        attachments=attachments,
        has_remote_images=raw is not None and has_remote_images(raw),
        gmail_url=gmail_url(account.email, row.provider_id)
        if row.state == MailState.IN_MAIL
        else None,
        file_missing=raw is None,
    )


@router.get("/messages/{message_id}", response_model=MessageOut)
def message(
    message_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    row, account = message_or_404(db, message_id)
    return message_out(request, db, row, account)


@router.get("/threads/{thread_id}", response_model=list[MessageOut])
def thread(
    thread_id: str,
    account_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    account = account_or_404(db, account_id)
    if valid_mail_id(thread_id) is None:
        raise HTTPException(404, "thread not found")
    rows = db.scalars(
        select(MailMessage)
        .where(MailMessage.account_id == account_id, MailMessage.thread_id == thread_id)
        .order_by(MailMessage.date, MailMessage.id)
    )
    return [message_out(request, db, r, account) for r in rows]


@router.get("/messages/{message_id}/attachments/{index}")
def attachment(
    message_id: int,
    index: int,
    request: Request,
    download: bool = False,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    row, account = message_or_404(db, message_id)
    try:
        name, ctype, data = attachment_of(eml_bytes(request, account, row), index)
    except IndexError:
        raise HTTPException(404, "attachment not found") from None
    served, inline_ok = serve_policy(ctype)
    show = inline_ok and can_preview(ctype) and not download
    return Response(
        data,
        media_type=served,
        headers={
            "Content-Security-Policy": csp_for(served),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Disposition": content_disposition("inline" if show else "attachment", name),
        },
    )


@router.get("/messages/{message_id}/eml")
def eml(
    message_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    row, account = message_or_404(db, message_id)
    name = "".join(c for c in (row.subject or "mail") if c not in '/\\:*?"<>|' and c.isprintable())
    name = name.strip()[:80] or "mail"
    return Response(
        eml_bytes(request, account, row),
        media_type="message/rfc822",
        headers={
            "Content-Disposition": content_disposition("attachment", f"{name}.eml"),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
