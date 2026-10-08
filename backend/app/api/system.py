import platform

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import __version__
from app.auth.deps import AuthContext, get_db, require_user
from app.schemas.system import SystemOut
from app.services.app_settings import get_all, set_settings
from app.services.rclone.commands import version_args
from app.services.rclone.runner import run_rclone

router = APIRouter()


async def _rclone_version(request: Request) -> str:
    cached = getattr(request.app.state, "rclone_version", None)
    if cached:
        return cached
    env = request.app.state.rclone_env
    out: list[str] = []
    try:
        res = await run_rclone(version_args(env), env.env(), log_path=None, on_stdout=out.append)
    except OSError:
        return "unknown"
    if res.exit_code != 0 or not out:
        return "unknown"  # not cached, so a later call can try again
    request.app.state.rclone_version = out[0].strip()
    return request.app.state.rclone_version


@router.get("/api/system", response_model=SystemOut)
async def system(request: Request, ctx: AuthContext = Depends(require_user)):
    st = request.app.state
    watcher = getattr(st, "watcher", None)
    return SystemOut(
        app_name=st.settings.app_name,
        app_version=__version__,
        python_version=platform.python_version(),
        rclone_version=await _rclone_version(request),
        storage_root=str(st.layout.storage),
        data_dir=str(st.layout.data),
        watcher=watcher.status if watcher else "off",
        db_ok=st.db_ok,
        insecure_http=st.settings.insecure_http,
        public_url=st.settings.public_url,
    )


@router.get("/api/settings")
def read_settings(ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)):
    return get_all(db)


@router.put("/api/settings")
def write_settings(
    body: dict, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)
):
    try:
        return set_settings(db, body)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
