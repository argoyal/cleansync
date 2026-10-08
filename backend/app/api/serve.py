"""Open and download files from the browser: inline view with Range, download, folder zip."""

import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.files import clean_path, get_scope
from app.auth.deps import AuthContext, get_db, require_user
from app.core.pathsafe import UnsafePath, safe_join
from app.services.files.mimes import guess_mime, serve_policy
from app.services.files.scope import is_hidden
from app.services.files.thumbs import ThumbBusy, ThumbError
from app.services.files.zipstream import zip_folder

router = APIRouter()

# Opened files run on DriveSync's address, so they get a sandbox: no script, no forms,
# no access to DriveSync's cookies or pages. Chrome's PDF viewer does not work inside a
# sandbox; a PDF cannot run script on our pages in Chrome, so it only gets frame-ancestors.
FILE_CSP = (
    "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; "
    "style-src 'unsafe-inline'; frame-ancestors 'none'"
)
PDF_CSP = "frame-ancestors 'none'"
# Video and audio cannot run script. A sandboxed page has an opaque origin and may drop the
# session cookie on Range requests, which would break seeking, so media gets the light policy.
NO_SANDBOX_TYPES = ("application/pdf",)


def _csp_for(served: str) -> str:
    if served in NO_SANDBOX_TYPES or served.startswith(("video/", "audio/")):
        return PDF_CSP
    return FILE_CSP


def _resolves_to_hidden(root: Path, target: Path) -> bool:
    """True when a symlink inside the account leads into a hidden name."""
    try:
        rel = os.path.relpath(target.resolve(), root.resolve())
    except (OSError, RuntimeError, ValueError):
        return True
    return is_hidden(rel.replace(os.sep, "/"))


def content_disposition(kind: str, filename: str) -> str:
    fallback = "".join(
        c if c.isascii() and c not in '"\\' and c.isprintable() else "_" for c in filename
    )
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/files/{account_id}/{path:path}")
def serve_file(
    account_id: int,
    path: str,
    request: Request,
    download: bool = False,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    scope = get_scope(request, db, account_id)
    rel = clean_path(path)
    try:
        target = safe_join(scope.root, rel)
    except UnsafePath as e:
        raise HTTPException(422, str(e)) from e
    if not rel or not target.is_file() or _resolves_to_hidden(scope.root, target):
        raise HTTPException(404, "file not found")
    served, inline_ok = serve_policy(guess_mime(target.name))
    kind = "inline" if inline_ok and not download else "attachment"
    headers = {
        "Content-Security-Policy": _csp_for(served),
        "Cache-Control": "private, no-cache",
        "Content-Disposition": content_disposition(kind, target.name),
    }
    # FileResponse answers Range requests itself (206 Partial Content), so video can seek.
    return FileResponse(target, media_type=served, headers=headers)


@router.get("/api/files/zip")
def download_zip(
    account_id: int,
    request: Request,
    path: str = "",
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    scope = get_scope(request, db, account_id)
    rel = clean_path(path)
    try:
        folder = safe_join(scope.root, rel)
    except UnsafePath as e:
        raise HTTPException(422, str(e)) from e
    if not folder.is_dir() or _resolves_to_hidden(scope.root, folder):
        raise HTTPException(404, "folder not found")
    name = folder.name if rel else scope.account.email
    return StreamingResponse(
        zip_folder(folder, name),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition("attachment", f"{name}.zip"),
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/api/thumbs/{account_id}/{path:path}")
async def thumbnail(
    account_id: int,
    path: str,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    scope = get_scope(request, db, account_id)
    rel = clean_path(path)
    try:
        target = safe_join(scope.root, rel)
    except UnsafePath as e:
        raise HTTPException(422, str(e)) from e
    if not rel or not target.is_file() or _resolves_to_hidden(scope.root, target):
        raise HTTPException(404, "file not found")
    try:
        out = await request.app.state.thumbs.get(target)
    except ThumbBusy as e:
        raise HTTPException(503, str(e)) from e
    except (ThumbError, OSError) as e:
        raise HTTPException(404, str(e)) from e
    # The UI adds ?v=<modified time> to the address, so a changed file gets a new preview.
    return FileResponse(
        out, media_type="image/webp", headers={"Cache-Control": "private, max-age=604800"}
    )
