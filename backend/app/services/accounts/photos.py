"""Each mailbox's Google profile photo (0.5.2 spec §8): fetched, checked, kept in the account's
folder and served by CleanSync itself (the page only allows images from its own address)."""

import logging
import os
from collections.abc import Callable
from io import BytesIO
from urllib.parse import urlparse

import httpx
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from app.models import Account
from app.paths import Layout

logger = logging.getLogger(__name__)

PHOTO_MAX = 256 * 1024
PHOTO_HOST_SUFFIX = ".googleusercontent.com"
_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def photo_http_factory() -> httpx.AsyncClient:
    """A plain client for the photo: short timeout, no redirects, no proxy settings from the
    environment."""
    return httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)


def allowed_photo_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parts = urlparse(url)
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme == "https" and bool(host) and host.endswith(PHOTO_HOST_SUFFIX)


def photo_path(layout: Layout, email: str):
    return layout.account_dir(email) / "photo"


def photo_exists(layout: Layout, acc: Account) -> bool:
    return bool(acc.photo_type) and photo_path(layout, acc.email).is_file()


async def _download(url: str, http_factory: Callable[[], httpx.AsyncClient]) -> bytes | None:
    async with http_factory() as http:
        async with http.stream("GET", url, follow_redirects=False) as resp:
            if resp.status_code != 200:
                return None
            data = bytearray()
            async for chunk in resp.aiter_bytes():
                data += chunk
                if len(data) > PHOTO_MAX:  # never hold more than one byte past the limit
                    return None
            return bytes(data)


def _image_type(data: bytes) -> str | None:
    with Image.open(BytesIO(data)) as img:
        fmt = img.format
        img.verify()
    return _TYPES.get(fmt or "")


async def save_photo(
    layout: Layout,
    sf: sessionmaker[Session],
    account_id: int,
    email: str,
    url: str | None,
    http_factory: Callable[[], httpx.AsyncClient],
    clock,
) -> bool:
    """Download, check and keep the photo. Never raises: any failure returns False and the old
    photo (if any) stays."""
    tmp = None
    try:
        if not allowed_photo_url(url):
            return False
        data = await _download(url, http_factory)  # type: ignore[arg-type]
        if not data:
            return False
        kind = _image_type(data)
        if kind is None:
            return False
        target = photo_path(layout, email)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name("photo.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        tmp = None
        with sf() as db:
            acc = db.get(Account, account_id)
            if acc is None:
                return False
            acc.photo_type = kind
            acc.photo_updated_at = clock()
            db.commit()
        return True
    except Exception as e:
        logger.warning("could not save the profile photo: %s", type(e).__name__)
        return False
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
