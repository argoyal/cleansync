"""Mail backup (spec §4): set up, start, check now, status, job logs."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_db, require_user
from app.models import MailJob
from app.schemas.mail import AccountIn, MailStatusOut, QueuedOut, SetupInfoOut
from app.services.mail.service import MailError

router = APIRouter(prefix="/api/mail")


def _mail(request: Request):
    return request.app.state.mail


@router.get("/status", response_model=MailStatusOut)
def status(account_id: int, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        return _mail(request).status(account_id)
    except MailError as e:
        raise HTTPException(e.status, str(e)) from e


@router.get("/overview", response_model=list[MailStatusOut])
def overview(request: Request, ctx: AuthContext = Depends(require_user)):
    return _mail(request).overview()


@router.get("/setup-info", response_model=SetupInfoOut)
async def setup_info(account_id: int, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        info = await _mail(request).setup_info(account_id)
    except MailError as e:
        raise HTTPException(e.status, str(e)) from e
    return SetupInfoOut(total=info.total, size_bytes=info.size_bytes)


@router.post("/start", status_code=202, response_model=QueuedOut)
async def start(body: AccountIn, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        return QueuedOut(queued=await _mail(request).start(body.account_id))
    except MailError as e:
        raise HTTPException(e.status, str(e)) from e


@router.post("/check", status_code=202, response_model=QueuedOut)
async def check(body: AccountIn, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        return QueuedOut(queued=await _mail(request).check_now(body.account_id))
    except MailError as e:
        raise HTTPException(e.status, str(e)) from e


@router.get("/jobs/{job_id}/log")
def job_log_file(
    job_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    job = db.get(MailJob, job_id)
    path = request.app.state.layout.mail_job_log(job_id)
    if job is None or not Path(path).exists():
        raise HTTPException(404, "log not found")
    return FileResponse(path, media_type="text/plain", filename=f"cleansync-mail-job{job_id}.log")
