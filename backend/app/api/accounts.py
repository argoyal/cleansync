import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.models import Account
from app.schemas.accounts import AccountOut, AuthUrlOut
from app.services.accounts.oauth import OAuthError
from app.services.accounts.service import AccountError

logger = logging.getLogger(__name__)
router = APIRouter()


def _out(acc: Account) -> AccountOut:
    return AccountOut.model_validate(acc, from_attributes=True)


@router.get("/api/accounts", response_model=list[AccountOut])
def list_accounts(ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)):
    return [_out(a) for a in db.scalars(select(Account).order_by(Account.email))]


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
    try:
        acc = await service.finish_connect(state, code)
    except OAuthError as e:
        return RedirectResponse(f"/?oauth_error={quote(str(e))}", 303)
    except Exception:
        logger.exception("connecting a Google account failed")
        msg = "Connecting the Google account failed; see the logs."
        return RedirectResponse(f"/?oauth_error={quote(msg)}", 303)
    request.app.state.catalog.refresh_account(acc.id)  # a reconnect may have missed changes
    return RedirectResponse(f"/?connected={acc.id}", 303)


@router.post("/api/accounts/{account_id}/disconnect", response_model=AccountOut)
async def disconnect(account_id: int, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        return _out(await request.app.state.accounts.disconnect(account_id))
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except AccountError as e:
        raise HTTPException(502, str(e)) from e


@router.get("/api/accounts/{account_id}/quota", response_model=AccountOut)
async def quota(account_id: int, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        return _out(await request.app.state.accounts.refresh_quota(account_id))
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
