import asyncio
import contextlib
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from app.core.redact import redact
from app.services.rclone.parse import LogLine, parse_line

_ERROR_LEVELS = {"error", "critical"}
_PLAIN_ERROR = re.compile(r"error|fatal|critical", re.IGNORECASE)
_MAX_LINE = 64 * 1024
_TRUNCATED = "...[truncated]"
_STOP_GRACE_S = 30


@dataclass
class RunResult:
    exit_code: int
    duration_s: float
    error_tail: list[str]


async def _read_lines(stream: asyncio.StreamReader) -> AsyncIterator[bytes]:
    """Yield lines, keeping at most _MAX_LINE bytes of an over-long line and skipping the rest."""
    while True:
        kept = bytearray()
        truncated = False
        eof = False
        while True:
            try:
                chunk = await stream.readuntil(b"\n")
                done = True
            except asyncio.LimitOverrunError as e:
                chunk = await stream.readexactly(e.consumed)
                done = False
            except asyncio.IncompleteReadError as e:
                chunk, done, eof = e.partial, True, True
            room = _MAX_LINE - len(kept)
            if len(chunk) > room:
                truncated = True
            if room > 0:
                kept += chunk[:room]
            if done:
                break
        if eof and not kept and not truncated:
            return
        line = kept.decode("utf-8", "replace").rstrip("\n")
        yield (line + _TRUNCATED if truncated else line).encode()
        if eof:
            return


async def _drain(stream: asyncio.StreamReader | None) -> None:
    if stream is None:
        return
    while await stream.read(65536):
        pass


async def _reap(proc: asyncio.subprocess.Process) -> None:
    """Never leave rclone running: ask it to stop, keep its pipes empty, then kill."""
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    drains = [
        asyncio.ensure_future(_drain(proc.stdout)),
        asyncio.ensure_future(_drain(proc.stderr)),
    ]
    try:
        await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_S)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
    finally:
        for d in drains:
            d.cancel()
        await asyncio.gather(*drains, return_exceptions=True)


def _is_error(line: LogLine) -> bool:
    if line.level in _ERROR_LEVELS:
        return True
    if line.raw.lstrip().startswith("{"):
        return line.level == "notice" and line.msg.startswith("Failed to ")
    return bool(_PLAIN_ERROR.search(line.msg)) or line.msg.startswith("Error:")


async def run_rclone(
    args: list[str],
    env: dict[str, str],
    *,
    log_path: Path | None,
    on_line: Callable[[LogLine], None] | None = None,
    on_stdout: Callable[[str], None] | None = None,
    secrets: Iterable[str] = (),
) -> RunResult:
    secrets = [s for s in secrets if s]
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *args,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=1024 * 1024,
    )
    errors: deque[str] = deque(maxlen=50)
    log_fh = None
    pumps: list[asyncio.Future] = []

    async def pump_stderr() -> None:
        assert proc.stderr is not None
        async for raw in _read_lines(proc.stderr):
            text = redact(raw.decode("utf-8", "replace"), secrets)
            if log_fh:
                log_fh.write(text + "\n")
            line = parse_line(text)
            if _is_error(line):
                errors.append(line.msg)
            if on_line:
                on_line(line)

    async def pump_stdout() -> None:
        assert proc.stdout is not None
        async for raw in _read_lines(proc.stdout):
            if on_stdout:
                on_stdout(raw.decode("utf-8", "replace"))

    try:
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        pumps = [asyncio.ensure_future(pump_stderr()), asyncio.ensure_future(pump_stdout())]
        await asyncio.gather(*pumps)
        code = await proc.wait()
    finally:
        for p in pumps:
            if not p.done():
                p.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
        try:
            await _reap(proc)
        finally:
            if log_fh:
                log_fh.close()
    return RunResult(exit_code=code, duration_s=time.monotonic() - started, error_tail=list(errors))
