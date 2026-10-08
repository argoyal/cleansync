"""Which account folder and which sync pair a path from the UI belongs to.

Paths from the UI are "account paths": relative to storage/<email>/, '/'-separated.
A "pair path" is relative to a pair's own folder (what rclone and the catalog use).
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, SyncPair
from app.paths import Layout

# Never listed, served, renamed or deleted from the UI.
HIDDEN_NAMES = frozenset({"RCLONE_TEST", ".google-exports"})
_HIDDEN_FOLDED = frozenset(n.casefold() for n in HIDDEN_NAMES)
# Half-placed upload copies (see app.services.files.upload); never synced either.
# Matched case-sensitively, the same way rclone's filter matches them, so the UI hides
# exactly what never syncs.
UPLOAD_TEMP_PREFIX = ".drivesync-upload-"
_UPLOAD_TEMP_NAME = re.compile(re.escape(UPLOAD_TEMP_PREFIX) + r"[0-9a-f]{32}")


def is_upload_temp_name(name: str) -> bool:
    """Exactly the name the app gives its own upload copies (prefix + uuid4 hex)."""
    return _UPLOAD_TEMP_NAME.fullmatch(name) is not None


def is_hidden_name(name: str) -> bool:
    """The fixed hidden names fold case (the disk may be case-insensitive: Windows, macOS);
    the upload-copy prefix does not, to match the sync filter."""
    return name.casefold() in _HIDDEN_FOLDED or name.startswith(UPLOAD_TEMP_PREFIX)


def is_hidden(rel: str) -> bool:
    return any(is_hidden_name(part) for part in rel.split("/"))


def resolves_to_hidden(root: Path, target: Path) -> bool:
    """True when a symlink inside the account leads into a hidden name (or can't be resolved)."""
    try:
        rel = os.path.relpath(target.resolve(), root.resolve())
    except (OSError, RuntimeError, ValueError):
        return True
    return is_hidden(rel.replace(os.sep, "/"))


def join_rel(base: str, name: str) -> str:
    return f"{base}/{name}" if base else name


def parent_rel(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def pair_for(pairs: list[SyncPair], rel: str) -> SyncPair | None:
    """The pair whose folder holds this path (pairs never overlap, so at most one)."""
    for p in pairs:
        s = p.local_subpath
        if s == "" or rel == s or rel.startswith(s + "/"):
            return p
    return None


def pairs_inside(pairs: list[SyncPair], rel: str) -> list[SyncPair]:
    """Pairs whose folder is strictly below this path."""
    return [
        p
        for p in pairs
        if p.local_subpath
        and p.local_subpath != rel
        and (rel == "" or p.local_subpath.startswith(rel + "/"))
    ]


def touches_pair_root(pairs: list[SyncPair], rel: str) -> bool:
    """True when this path is a pair's folder or contains one (or is the account root)."""
    if rel == "":
        return True
    return any(p.local_subpath == rel or p.local_subpath.startswith(rel + "/") for p in pairs)


def to_pair_rel(pair: SyncPair, rel: str) -> str:
    s = pair.local_subpath
    return rel if not s else rel[len(s) :].lstrip("/")


def to_account_rel(pair: SyncPair, prel: str) -> str:
    return join_rel(pair.local_subpath, prel) if prel else pair.local_subpath


@dataclass
class Scope:
    account: Account
    root: Path  # storage/<email>/
    pairs: list[SyncPair]


def load_scope(
    db: Session, layout: Layout, account_id: int, *, create_root: bool = True
) -> Scope | None:
    account = db.get(Account, account_id)
    if account is None:
        return None
    pairs = list(
        db.scalars(select(SyncPair).where(SyncPair.account_id == account.id).order_by(SyncPair.id))
    )
    root = layout.account_dir(account.email)
    if create_root:
        root.mkdir(parents=True, exist_ok=True)
    return Scope(account=account, root=root, pairs=pairs)
