"""Small previews for the grid view: images with Pillow, a PDF's first page with pdftoppm.

Made on first request, cached under app/data/thumbs keyed by path, size and modified time,
so a changed file gets a new preview. The cache is trimmed daily (prune_thumbs).
"""

import asyncio
import hashlib
import logging
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageOps

from app.services.files.mimes import THUMB_IMAGE_TYPES, can_thumb, guess_mime

log = logging.getLogger(__name__)
THUMB_PX = 256
MAX_PIXELS = 40_000_000  # refuse "decompression bombs": tiny files that unpack to huge images
PDF_TIMEOUT_S = 20.0
QUEUE_WAIT_S = 10.0  # how long a request waits for a free slot before "busy"
FAIL_MEMORY_S = 3600.0  # a file that failed is not retried for an hour
STALE_TMP_S = 3600.0
# Only these decoders may open a file, so a renamed EPS/PS can never reach Ghostscript.
IMAGE_FORMATS = ["JPEG", "PNG", "GIF", "WEBP", "BMP", "TIFF"]
assert len(THUMB_IMAGE_TYPES) == 6  # keep IMAGE_FORMATS in step with the mime list
CACHE_MAX_BYTES = 512 * 1024 * 1024
CACHE_KEEP_BYTES = 384 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ThumbError(Exception):
    pass


class ThumbBusy(Exception):
    """Too many previews are being made right now."""


def _save_webp(im: Image.Image, out: Path) -> None:
    tmp = out.with_name(f".{out.name}.{secrets.token_hex(4)}.tmp")
    try:
        im.save(tmp, "WEBP", quality=80)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)


def image_thumb(src: Path, out: Path) -> None:
    try:
        with Image.open(src, formats=IMAGE_FORMATS) as opened:
            if opened.width * opened.height > MAX_PIXELS:
                raise ThumbError("the image is too large to preview")
            opened.draft("RGB", (THUMB_PX * 2, THUMB_PX * 2))  # fast path for JPEG
            im = ImageOps.exif_transpose(opened)
            im.thumbnail((THUMB_PX, THUMB_PX))
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            _save_webp(im, out)
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        raise ThumbError(f"could not read the image: {e}") from e


class Thumbnails:
    def __init__(self, cache_dir: Path, *, pdftoppm: str = "pdftoppm", max_parallel: int = 2):
        self.dir = cache_dir
        self.pdftoppm = pdftoppm
        self._max_parallel = max_parallel
        self._sem: asyncio.Semaphore | None = None
        self._failed: dict[Path, tuple[float, str]] = {}

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._max_parallel)
        return self._sem

    def cache_path(self, src: Path, st: os.stat_result) -> Path:
        key = hashlib.sha256(
            f"{src}\0{st.st_size}\0{st.st_mtime_ns}\0{THUMB_PX}".encode()
        ).hexdigest()
        return self.dir / key[:2] / f"{key}.webp"

    def _lookup(self, src: Path) -> tuple[Path, str | None, bool]:
        """(cache path, type, already made); disk work, so it runs in a worker thread."""
        st = src.stat()
        mime = guess_mime(src.name)
        if not can_thumb(mime, st.st_size):
            raise ThumbError("no preview for this kind of file")
        out = self.cache_path(src, st)
        if not out.exists():
            return out, mime, False
        try:
            os.utime(out)  # recently used: kept longest when the cache is trimmed
        except OSError:
            pass
        return out, mime, True

    async def get(self, src: Path) -> Path:
        out, mime, cached = await asyncio.to_thread(self._lookup, src)
        if cached:
            return out
        failed = self._failed.get(out)
        if failed and failed[0] > time.monotonic():
            raise ThumbError(failed[1])
        sem = self._semaphore()
        try:
            await asyncio.wait_for(sem.acquire(), QUEUE_WAIT_S)
        except TimeoutError:
            raise ThumbBusy("busy, try again") from None
        try:
            if not await asyncio.to_thread(out.exists):
                await asyncio.to_thread(out.parent.mkdir, parents=True, exist_ok=True)
                try:
                    if mime == "application/pdf":
                        await self._pdf(src, out)
                    else:
                        await asyncio.to_thread(image_thumb, src, out)
                except ThumbError as e:
                    if len(self._failed) > 5000:
                        self._failed.clear()
                    self._failed[out] = (time.monotonic() + FAIL_MEMORY_S, str(e))
                    raise
        finally:
            sem.release()
        return out

    async def _pdf(self, src: Path, out: Path) -> None:
        with tempfile.TemporaryDirectory(dir=self.dir) as tmp:
            prefix = Path(tmp) / "page"
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.pdftoppm,
                    "-f",
                    "1",
                    "-l",
                    "1",
                    "-singlefile",
                    "-scale-to",
                    str(THUMB_PX),
                    "-png",
                    str(src),
                    str(prefix),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except FileNotFoundError as e:
                raise ThumbError("PDF previews need poppler-utils (pdftoppm)") from e
            try:
                try:
                    code = await asyncio.wait_for(proc.wait(), PDF_TIMEOUT_S)
                except TimeoutError:
                    raise ThumbError("the PDF took too long to preview") from None
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            png = prefix.with_suffix(".png")
            if code != 0 or not png.is_file():
                raise ThumbError("could not preview the PDF")
            await asyncio.to_thread(image_thumb, png, out)


def prune_thumbs(
    cache_dir: Path, max_bytes: int = CACHE_MAX_BYTES, keep_bytes: int = CACHE_KEEP_BYTES
) -> int:
    """When the cache is over max_bytes, delete least recently used previews down to keep_bytes."""
    cutoff = time.time() - STALE_TMP_S
    for p in [*cache_dir.rglob("*.tmp"), *cache_dir.glob("tmp*")]:
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
        except OSError:
            pass
    files = []
    for p in cache_dir.rglob("*.webp"):
        try:
            st = p.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, p))
    total = sum(size for _, size, _ in files)
    if total <= max_bytes:
        return 0
    removed = 0
    for _, size, p in sorted(files):
        if total <= keep_bytes:
            break
        p.unlink(missing_ok=True)
        total -= size
        removed += 1
    return removed
