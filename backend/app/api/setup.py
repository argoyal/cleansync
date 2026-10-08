"""What the first-run wizard needs to know before it starts."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.models import Account, SyncPair
from app.models.enums import AccountStatus

router = APIRouter()


class SetupOut(BaseModel):
    oauth_configured: bool
    redirect_uri: str  # what to paste into the Google OAuth client
    public_url: str
    insecure_http: bool
    accounts: int
    pairs: int
    initialized_pairs: int


@router.get("/api/setup", response_model=SetupOut)
def setup(
    request: Request, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)
):
    s = request.app.state.settings

    def count(model, *where) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    return SetupOut(
        oauth_configured=bool(s.google_client_id and s.google_client_secret),
        redirect_uri=s.oauth_redirect_uri,
        public_url=s.public_url,
        insecure_http=s.insecure_http,
        accounts=count(Account, Account.status != AccountStatus.DISCONNECTED),
        pairs=count(SyncPair),
        initialized_pairs=count(SyncPair, SyncPair.initialized.is_(True)),
    )
