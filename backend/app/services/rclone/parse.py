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
_ROOTS = re.compile(r'^Synching Path1 "(.*)" with Path2 "(.*)"$')
_CHECK = re.compile(r"^([=+\-*!]) (.+)$")


@dataclass
class LogLine:
    level: str
    msg: str
    obj: str | None
    stats: dict | None
    raw: str


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
    _roots: tuple[str, str] | None = field(default=None, repr=False)

    def _rel(self, path: str) -> str:
        """Drop the {pathN/} prefix, or the absolute root rclone prints for local-to-local runs."""
        path = _PREFIX.sub("", path.strip())
        for root in self._roots or ():
            if root and path.startswith(root):
                return path[len(root) :]
        return path

    @property
    def deletions(self) -> int:
        return self.deleted_local + self.deleted_remote

    def feed(self, line: LogLine) -> None:
        r = _ROOTS.match(line.msg)
        if r:
            self._roots = (r.group(1), r.group(2))
            return
        m = _QUEUE.match(line.msg)
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
        b = _BOTH.match(line.msg)
        if b:
            self.conflict_paths.append(self._rel(b.group(1)))


def count_dry_run_deletes(line: LogLine) -> bool:
    return line.msg.startswith("Skipped delete as --dry-run is set")


def parse_check_combined(line: str) -> tuple[str, str] | None:
    m = _CHECK.match(line.rstrip("\n"))
    return (m.group(1), m.group(2)) if m else None
