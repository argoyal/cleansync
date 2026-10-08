"""The cached Google Drive listing of one sync pair (spec §6.6).

Built from `rclone lsjson -R --fast-list --metadata`. Drive-only fields (ID, Google MIME
types, "starred") are optional, so local or alias remotes work too (dev, tests).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath

GOOGLE_PREFIX = "application/vnd.google-apps."
_NATIVE_BY_MIME = {
    "document": "document",
    "spreadsheet": "spreadsheet",
    "presentation": "presentation",
    "drawing": "drawing",
    "form": "form",
    "site": "site",
}
# rclone lists a native doc under the name of its export format (docx, xlsx, pptx, svg).
_NATIVE_BY_EXT = {
    ".docx": "document",
    ".xlsx": "spreadsheet",
    ".pptx": "presentation",
    ".svg": "drawing",
}
# The listed name rclone gives a native doc we create ourselves (default export formats).
EXPORT_EXT = {"document": ".docx", "spreadsheet": ".xlsx", "presentation": ".pptx"}
_OFFICE_APP = {
    ".docx": "document",
    ".doc": "document",
    ".xlsx": "spreadsheets",
    ".xls": "spreadsheets",
    ".pptx": "presentation",
    ".ppt": "presentation",
}
_EDITOR = {
    "document": "document",
    "spreadsheet": "spreadsheets",
    "presentation": "presentation",
    "drawing": "drawings",
}
_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_TRUE = {"true", "1", "yes"}


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    path: str
    is_dir: bool
    size: int
    mtime: datetime | None
    id: str | None = None
    mime: str | None = None
    starred: bool = False
    native: str | None = None  # "document", "spreadsheet", ... for Google-native files

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def parent(self) -> str:
        parent = str(PurePosixPath(self.path).parent)
        return "" if parent == "." else parent

    @property
    def display_name(self) -> str:
        """Native docs without the export extension rclone adds ("Report", not "Report.docx")."""
        if self.native:
            p = PurePosixPath(self.name)
            if p.suffix.lower() in _NATIVE_BY_EXT:
                return p.stem
        return self.name

    def to_json(self) -> dict:
        return {
            "path": self.path,
            "dir": self.is_dir,
            "size": self.size,
            "mtime": self.mtime.isoformat() if self.mtime else None,
            "id": self.id,
            "mime": self.mime,
            "starred": self.starred,
            "native": self.native,
        }

    @classmethod
    def from_json(cls, d: dict) -> "CatalogEntry":
        if not valid_path(d["path"]):
            raise ValueError("bad path in the saved listing")
        return cls(
            path=d["path"],
            is_dir=bool(d["dir"]),
            size=int(d["size"]),
            mtime=datetime.fromisoformat(d["mtime"]) if d.get("mtime") else None,
            id=valid_id(d.get("id")),
            mime=d.get("mime"),
            starred=bool(d.get("starred")),
            native=d.get("native"),
        )


def valid_path(path: object) -> bool:
    return (
        isinstance(path, str)
        and bool(path)
        and not path.startswith("/")
        and "\x00" not in path
        and ".." not in path.split("/")
    )


def valid_id(obj: object) -> str | None:
    return obj if isinstance(obj, str) and _ID.match(obj) else None


def native_kind(item: dict) -> str | None:
    if item.get("IsDir"):
        return None
    mime = str(item.get("MimeType") or "")
    if mime.startswith(GOOGLE_PREFIX):
        return _NATIVE_BY_MIME.get(mime[len(GOOGLE_PREFIX) :], "other")
    if item.get("Size") == -1:  # Drive reports no size for native docs
        return _NATIVE_BY_EXT.get(PurePosixPath(str(item.get("Name", ""))).suffix.lower(), "other")
    return None


def _mtime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_item(item: dict) -> CatalogEntry | None:
    """One line of `rclone lsjson` output, or None when it has no usable path."""
    path = item.get("Path")
    if not valid_path(path):
        return None
    meta = item.get("Metadata") if isinstance(item.get("Metadata"), dict) else {}
    raw_id = item.get("ID")
    mime = item.get("MimeType")
    size = item.get("Size")
    return CatalogEntry(
        path=path,
        is_dir=bool(item.get("IsDir")),
        size=size if isinstance(size, int) and size >= 0 else 0,
        mtime=_mtime(item.get("ModTime")),
        id=valid_id(raw_id),
        mime=mime if isinstance(mime, str) else None,
        starred=str(meta.get("starred", "")).lower() in _TRUE,
        native=native_kind(item),
    )


def google_url(entry: CatalogEntry) -> str | None:
    """Where "Open in Google" goes; None when the listing has no Drive ID."""
    if entry.id is None:
        return None
    i = entry.id
    if entry.is_dir:
        return f"https://drive.google.com/drive/folders/{i}"
    if entry.native in _EDITOR:
        return f"https://docs.google.com/{_EDITOR[entry.native]}/d/{i}/edit"
    if entry.native is not None:
        return f"https://drive.google.com/open?id={i}"
    app = _OFFICE_APP.get(PurePosixPath(entry.name).suffix.lower())
    if app:  # Office file: Google's editor in Office mode
        return f"https://docs.google.com/{app}/d/{i}/edit?rtpof=true&sd=true"
    return f"https://drive.google.com/file/d/{i}/view"


@dataclass
class Catalog:
    pair_id: int
    refreshed_at: datetime
    root_id: str | None  # Drive ID of the pair's own folder ("root" for all of My Drive)
    files: dict[str, CatalogEntry]  # regular files and folders, by path
    natives: dict[str, CatalogEntry]  # Google-native files, by their listed path
    _children: dict[str, list[CatalogEntry]] | None = field(default=None, repr=False)

    @classmethod
    def build(
        cls,
        pair_id: int,
        refreshed_at: datetime,
        root_id: str | None,
        entries: Iterable[CatalogEntry],
    ) -> "Catalog":
        files: dict[str, CatalogEntry] = {}
        natives: dict[str, CatalogEntry] = {}
        for e in entries:
            (natives if e.native else files)[e.path] = e
        return cls(pair_id, refreshed_at, root_id, files, natives)

    def children(self, parent: str) -> list[CatalogEntry]:
        if self._children is None:
            index: dict[str, list[CatalogEntry]] = {}
            for e in (*self.files.values(), *self.natives.values()):
                index.setdefault(e.parent, []).append(e)
            self._children = index
        return self._children.get(parent, [])

    def folder_id(self, rel: str) -> str | None:
        if rel == "":
            return self.root_id
        entry = self.files.get(rel)
        return entry.id if entry is not None and entry.is_dir else None

    def summary(self) -> dict:
        regular = [e for e in self.files.values() if not e.is_dir]
        return {
            "files": len(regular),
            "folders": len(self.files) - len(regular),
            "bytes": sum(e.size for e in regular),
            "native_docs": len(self.natives),
        }

    def to_json(self) -> dict:
        return {
            "version": 1,
            "pair_id": self.pair_id,
            "refreshed_at": self.refreshed_at.isoformat(),
            "root_id": self.root_id,
            "entries": [e.to_json() for e in (*self.files.values(), *self.natives.values())],
        }

    @classmethod
    def from_json(cls, d: dict) -> "Catalog":
        return cls.build(
            int(d["pair_id"]),
            datetime.fromisoformat(d["refreshed_at"]),
            d.get("root_id"),
            (CatalogEntry.from_json(e) for e in d["entries"]),
        )
