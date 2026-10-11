import mimetypes
from pathlib import PurePosixPath

_EXTRA = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".log": "text/plain",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".mkv": "video/x-matroska",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".opus": "audio/ogg",
}

THUMB_IMAGE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/tiff"}
)
MAX_THUMB_IMAGE_BYTES = 80 * 1024 * 1024
MAX_THUMB_PDF_BYTES = 300 * 1024 * 1024


def guess_mime(name: str) -> str | None:
    ext = PurePosixPath(name).suffix.lower()
    if ext in _EXTRA:
        return _EXTRA[ext]
    mime, _ = mimetypes.guess_type(name, strict=False)
    return mime


def can_thumb(mime: str | None, size: int | None) -> bool:
    if size is None:
        return False
    if mime in THUMB_IMAGE_TYPES:
        return size <= MAX_THUMB_IMAGE_BYTES
    return mime == "application/pdf" and size <= MAX_THUMB_PDF_BYTES


_TEXT_LIKE = frozenset(
    {
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
    }
)


def serve_policy(mime: str | None) -> tuple[str, bool]:
    """(Content-Type to send, may the browser show it inline).

    Anything that could run script on CleanSync's own address (HTML, XML, JavaScript) is
    sent as plain text, and every inline response also gets a sandboxing CSP (api/serve.py).
    """
    if mime is None:
        return "application/octet-stream", False
    if mime == "application/pdf" or mime.startswith(("image/", "video/", "audio/")):
        return mime, True
    if mime.startswith("text/") or mime in _TEXT_LIKE:
        return "text/plain; charset=utf-8", True
    return mime, False
