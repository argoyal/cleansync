"""Parsing of rclone output.

Counters relies on bisync queue lines, which rclone prints at INFO level and above, and on the
"Synching Path1 ... with Path2 ..." line to make paths relative. commands.LOG_FLAGS sets INFO;
without it (and without that line) planned paths stay absolute.
"""

import json
import re
from dataclasses import dataclass, field

_TEXT_LEVEL = re.compile(r"\b(DEBUG|INFO|NOTICE|ERROR|CRITICAL)\b")
_QUEUE = re.compile(r"^-\s+(Path1|Path2)\s+Queue (delete|copy to Path1|copy to Path2)\s+-\s+(.+)$")
_BOTH = re.compile(r"^-\s+WARNING\s+New or changed in both paths\s+-\s+(.+)$")
_PREFIX = re.compile(r"^\{path[12]/\}")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_RENAME = re.compile(r"^-\s+(Path1|Path2)\s+Renaming (?:Path1|Path2) copy\s+-\s+(.+)$")
_SUCCESS = "Bisync successful"
_ROOTS = re.compile(r'^Synching Path1 "(.*)" with Path2 "(.*)"$')
_CHECK = re.compile(r"^([=+\-*!]) (.+)$")


@dataclass
class LogLine:
    level: str
    msg: str
    obj: str | None
    stats: dict | None
    raw: str
    # rclone's structured "skipped" field on dry-run lines ("delete", "copy", ...).
    skipped: str | None = None
    # Unmasked msg/object, set by the runner. msg/obj/raw are masked for logs and the UI;
    # these keep real file names (e.g. "token=abc.txt") for counting and planned paths.
    plain_msg: str | None = None
    plain_obj: str | None = None

    @property
    def text(self) -> str:
        return self.plain_msg if self.plain_msg is not None else self.msg

    @property
    def path_obj(self) -> str | None:
        return self.plain_obj if self.plain_obj is not None else self.obj


def parse_line(raw: str) -> LogLine:
    # rclone colours bisync messages even with --use-json-log; strip the escape codes.
    raw = _ANSI.sub("", raw.rstrip("\n"))
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return LogLine(
                level=str(data.get("level", "info")).lower(),
                msg=_ANSI.sub("", str(data.get("msg", ""))).strip(),
                obj=data.get("object"),
                stats=data.get("stats"),
                raw=raw,
                skipped=str(data["skipped"]) if data.get("skipped") is not None else None,
            )
    except ValueError:
        pass
    m = _TEXT_LEVEL.search(raw)
    return LogLine(
        level=m.group(1).lower() if m else "info", msg=raw.strip(), obj=None, stats=None, raw=raw
    )


@dataclass
class Progress:
    bytes: int
    total_bytes: int
    speed: float
    eta: float | None
    transferring: list[dict]


def progress_from_stats(stats: dict) -> Progress:
    return Progress(
        bytes=int(stats.get("bytes", 0)),
        total_bytes=int(stats.get("totalBytes", 0)),
        speed=float(stats.get("speed", 0.0)),
        eta=stats.get("eta"),
        transferring=list(stats.get("transferring") or []),
    )


@dataclass
class Counters:
    uploaded: int = 0
    downloaded: int = 0
    deleted_local: int = 0
    deleted_remote: int = 0
    conflict_paths: list[str] = field(default_factory=list)
    planned: list[tuple[str, str, str]] = field(default_factory=list)
    # conflict path -> (local variant, Drive variant), from rclone's "Renaming PathN copy" lines.
    conflict_variants: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    # The last stats block seen; rclone prints its final summary last.
    final_stats: dict | None = None
    bisync_successful: bool = False
    _last_conflict: str | None = field(default=None, repr=False)
    _roots: tuple[str, str] | None = field(default=None, repr=False)

    def _rel(self, path: str) -> str:
        """Drop the {pathN/} prefix, or the absolute root rclone prints for local-to-local runs."""
        path = _PREFIX.sub("", path.strip())
        for root in self._roots or ():
            if root and path.startswith(root):
                return path[len(root) :]
        return path

    @property
    def has_roots(self) -> bool:
        return self._roots is not None

    @property
    def deletions(self) -> int:
        return self.deleted_local + self.deleted_remote

    def feed(self, line: LogLine) -> None:
        text = line.text
        if line.stats is not None:
            self.final_stats = line.stats
        if text == _SUCCESS:
            self.bisync_successful = True
            return
        r = _ROOTS.match(text)
        if r:
            self._roots = (r.group(1), r.group(2))
            return
        m = _QUEUE.match(text)
        if m:
            side_label, action, path = m.group(1), m.group(2), self._rel(m.group(3))
            if action == "delete":
                side = "local" if side_label == "Path1" else "remote"
                if side == "local":
                    self.deleted_local += 1
                else:
                    self.deleted_remote += 1
                self.planned.append((side, "delete", path))
            elif action == "copy to Path2":
                self.uploaded += 1
                self.planned.append(("remote", "copy", path))
            else:
                self.downloaded += 1
                self.planned.append(("local", "copy", path))
            return
        b = _BOTH.match(text)
        if b:
            self._last_conflict = self._rel(b.group(1))
            self.conflict_paths.append(self._last_conflict)
            return
        rn = _RENAME.match(text)
        if rn and self._last_conflict is not None:
            local, remote = self.conflict_variants.get(self._last_conflict, (None, None))
            variant = self._rel(rn.group(2))
            if rn.group(1) == "Path1":
                local = variant
            else:
                remote = variant
            self.conflict_variants[self._last_conflict] = (local, remote)


def count_dry_run_deletes(line: LogLine) -> bool:
    """A one-way dry-run delete, read from rclone's structured "skipped" field."""
    return line.skipped == "delete"


def parse_check_combined(line: str) -> tuple[str, str] | None:
    m = _CHECK.match(line.rstrip("\n"))
    return (m.group(1), m.group(2)) if m else None
