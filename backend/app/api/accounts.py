import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.models import Account
from app.paths import Layout
from app.schemas.accounts import (
    AccountOut,
    AuthUrlOut,
    GmailFiltersIn,
    MailSetupOut,
    PermanentDeleteIn,
)
from app.services.accounts.filters import gmail_filters_on
from app.services.accounts.filters import turn_off as filters_turn_off
from app.services.accounts.oauth import (
    FILTERS_PURPOSE,
    GMAIL_SCOPE,
    PERMANENT_PURPOSE,
    OAuthError,
    has_scope,
)
from app.services.accounts.permanent import permanent_delete_on, turn_off
from app.services.accounts.photos import photo_exists, photo_path
from app.services.accounts.service import AccountError
from app.services.accounts.services import service_states

logger = logging.getLogger(__name__)
router = APIRouter()


def account_out(db: Session, acc: Account, layout: Layout) -> AccountOut:
    out = AccountOut.model_validate(acc, from_attributes=True)
    return out.model_copy(
        update={
            "services": service_states(db, acc.id),
            "mail_permission": has_scope(acc.scopes, GMAIL_SCOPE),
            "permanent_delete": permanent_delete_on(acc),
            "gmail_filters": gmail_filters_on(acc),
            "has_photo": photo_exists(layout, acc),
        }
    )


@router.get("/api/accounts", response_model=list[AccountOut])
def list_accounts(
    request: Request, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)
):
    layout = request.app.state.layout
    return [account_out(db, a, layout) for a in db.scalars(select(Account).order_by(Account.email))]


@router.get("/api/accounts/{account_id}/photo")
def account_photo(
    account_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    acc = db.get(Account, account_id)
    layout = request.app.state.layout
    if acc is None or not photo_exists(layout, acc):
        raise HTTPException(404, "no photo")
    try:
        data = photo_path(layout, acc.email).read_bytes()
    except OSError as e:
        raise HTTPException(404, "no photo") from e
    return Response(
        data,
        media_type=acc.photo_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.post("/api/accounts/connect", response_model=AuthUrlOut)
def connect(request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        return AuthUrlOut(auth_url=request.app.state.accounts.start_connect())
    except OAuthError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/accounts/{account_id}/reconnect", response_model=AuthUrlOut)
def reconnect(
    account_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    if db.get(Account, account_id) is None:
        raise HTTPException(404, "account not found")
    try:
        return AuthUrlOut(auth_url=request.app.state.accounts.start_connect(account_id))
    except OAuthError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/accounts/{account_id}/mail/setup", response_model=MailSetupOut)
def mail_setup(account_id: int, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        return MailSetupOut(auth_url=request.app.state.accounts.start_mail_setup(account_id))
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except (AccountError, OAuthError) as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/accounts/{account_id}/permanent-delete", response_model=MailSetupOut)
def permanent_delete(
    account_id: int,
    body: PermanentDeleteIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Allow permanent delete, per mailbox (bulk offload spec §3). auth_url: Google's
    sign-in that asks for the full Gmail permission; None when nothing more is needed."""
    acc = db.get(Account, account_id)
    if acc is None:
        raise HTTPException(404, "account not found")
    if not body.on:
        turn_off(db, account_id)
        # An old Google tab left open must not turn it back on.
        request.app.state.accounts.states.forget(account_id, PERMANENT_PURPOSE)
        request.app.state.bus.publish("account", {"account_id": account_id, "status": acc.status})
        return MailSetupOut(auth_url=None)
    try:
        return MailSetupOut(auth_url=request.app.state.accounts.start_permanent_delete(account_id))
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except (AccountError, OAuthError) as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/accounts/{account_id}/gmail-filters", response_model=MailSetupOut)
def gmail_filters(
    account_id: int,
    body: GmailFiltersIn,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Allow Gmail filters, per mailbox (0.5.2 spec §5). auth_url: Google's sign-in that asks
    for the Gmail settings permission; None when nothing more is needed."""
    acc = db.get(Account, account_id)
    if acc is None:
        raise HTTPException(404, "account not found")
    if not body.on:
        filters_turn_off(db, account_id)
        # An old Google tab left open must not turn it back on.
        request.app.state.accounts.states.forget(account_id, FILTERS_PURPOSE)
        request.app.state.bus.publish("account", {"account_id": account_id, "status": acc.status})
        return MailSetupOut(auth_url=None)
    try:
        return MailSetupOut(auth_url=request.app.state.accounts.start_gmail_filters(account_id))
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except (AccountError, OAuthError) as e:
        raise HTTPException(400, str(e)) from e


@router.get("/api/oauth/callback")
async def oauth_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    # No session check: Google's redirect is cross-site, so the SameSite=Strict cookie is not
    # sent. The single-use, 10-minute state (with PKCE) is what proves this request is ours.
    service = request.app.state.accounts
    if error:
        service.states.pop(state)
        msg = (
            "Google sign-in was cancelled."
            if error == "access_denied"
            else "Google sign-in failed."
        )
        return RedirectResponse(f"/?oauth_error={quote(msg)}", 303)
    pending = service.states.peek(state)
    purpose = pending.purpose if pending is not None else "connect"
    try:
        acc = await service.finish_connect(state, code)
    except OAuthError as e:
        return RedirectResponse(f"/?oauth_error={quote(str(e))}", 303)
    except Exception:
        logger.exception("connecting a Google account failed")
        msg = "Connecting the Google account failed; see the logs."
        return RedirectResponse(f"/?oauth_error={quote(msg)}", 303)
    request.app.state.catalog.refresh_account(acc.id)  # a reconnect may have missed changes
    if purpose == "mail":
        outcome = "ready" if has_scope(acc.scopes, GMAIL_SCOPE) else "denied"
        return RedirectResponse(f"/?connected={acc.id}&mail={outcome}", 303)
    if purpose == PERMANENT_PURPOSE:
        outcome = "on" if permanent_delete_on(acc) else "denied"
        return RedirectResponse(f"/?connected={acc.id}&permanent={outcome}", 303)
    if purpose == FILTERS_PURPOSE:
        outcome = "on" if gmail_filters_on(acc) else "denied"
        return RedirectResponse(f"/?connected={acc.id}&filters={outcome}", 303)
    return RedirectResponse(f"/?connected={acc.id}", 303)


@router.post("/api/accounts/{account_id}/disconnect", response_model=AccountOut)
async def disconnect(
    account_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        return account_out(
            db, await request.app.state.accounts.disconnect(account_id), request.app.state.layout
        )
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except AccountError as e:
        raise HTTPException(502, str(e)) from e


@router.get("/api/accounts/{account_id}/quota", response_model=AccountOut)
async def quota(
    account_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        return account_out(
            db, await request.app.state.accounts.refresh_quota(account_id), request.app.state.layout
        )
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
