from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.core.pathsafe import UnsafePath
from app.models import Account
from app.services.rclone.commands import UnsafeArgument
from app.services.sync.browse import BrowseError, list_local_dirs, list_remote_dirs

router = APIRouter()


def _account(db: Session, account_id: int) -> Account:
    acc = db.get(Account, account_id)
    if acc is None:
        raise HTTPException(404, "account not found")
    return acc


@router.get("/api/remote-browse")
async def remote_browse(
    account_id: int,
    request: Request,
    path: str = "",
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    acc = _account(db, account_id)
    try:
        return await list_remote_dirs(request.app.state.rclone_env, acc.remote_name, path)
    except (UnsafePath, UnsafeArgument) as e:
        raise HTTPException(422, str(e)) from e
    except BrowseError as e:
        raise HTTPException(502, f"Could not list Google Drive: {e}") from e


@router.get("/api/local-browse")
def local_browse(
    account_id: int,
    request: Request,
    path: str = "",
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    acc = _account(db, account_id)
    try:
        return list_local_dirs(request.app.state.layout, acc, path)
    except UnsafePath as e:
        raise HTTPException(422, str(e)) from e
