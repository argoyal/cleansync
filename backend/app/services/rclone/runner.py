import asyncio
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from app.core.redact import redact
from app.services.rclone.parse import LogLine, parse_line

_ERROR_LEVELS = {"error", "critical"}


@dataclass
class RunResult:
    exit_code: int
    duration_s: float
    error_tail: list[str]


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
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_path, "a", encoding="utf-8")  # noqa: SIM115

    async def pump_stderr() -> None:
        assert proc.stderr is not None
        async for raw in proc.stderr:
            text = redact(raw.decode("utf-8", "replace"), secrets)
            if log_fh:
                log_fh.write(text if text.endswith("\n") else text + "\n")
            line = parse_line(text)
            if line.level in _ERROR_LEVELS:
                errors.append(line.msg)
            if on_line:
                on_line(line)

    async def pump_stdout() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            text = raw.decode("utf-8", "replace").rstrip("\n")
            if on_stdout:
                on_stdout(text)

    try:
        await asyncio.gather(pump_stderr(), pump_stdout())
        code = await proc.wait()
    except asyncio.CancelledError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=30)
        except TimeoutError:
            proc.kill()
        raise
    finally:
        if log_fh:
            log_fh.close()
    return RunResult(exit_code=code, duration_s=time.monotonic() - started, error_tail=list(errors))
