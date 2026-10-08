import asyncio
import resource
import subprocess
import time
import uuid

import pytest

from app.services.rclone.runner import run_rclone
from tests.fake_rclone import jl


async def test_runner_streams_lines_to_log_and_callback(fake_rclone, tmp_path):
    fake_rclone.on(
        "lsjson",
        stderr=[jl("one"), jl("token ya29.secret", level="error")],
        stdout=['[{"Path":"a"}]'],
        exit=3,
    )
    seen, out = [], []
    log = tmp_path / "job.log"
    result = await run_rclone(
        [fake_rclone.binary, "lsjson", "gd:"],
        fake_rclone.env(),
        log_path=log,
        on_line=seen.append,
        on_stdout=out.append,
    )
    assert result.exit_code == 3
    assert [s.msg for s in seen][0] == "one"
    assert out == ['[{"Path":"a"}]']
    assert "ya29.secret" not in log.read_text()
    assert any("token" in e for e in result.error_tail)


async def test_runner_redacts_extra_secrets(fake_rclone, tmp_path):
    fake_rclone.on("about", stderr=[jl("pass is supersecretvalue")])
    log = tmp_path / "x.log"
    await run_rclone(
        [fake_rclone.binary, "about", "gd:"],
        fake_rclone.env(),
        log_path=log,
        secrets=["supersecretvalue"],
    )
    assert "supersecretvalue" not in log.read_text()


async def test_runner_streams_large_output_without_buffering(fake_rclone, tmp_path):
    fake_rclone.on("bisync", stderr=[jl(f"line {i} " + "x" * 80) for i in range(100_000)])
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    count = 0

    def on_line(_):
        nonlocal count
        count += 1

    result = await run_rclone(
        [fake_rclone.binary, "bisync"],
        fake_rclone.env(),
        log_path=tmp_path / "big.log",
        on_line=on_line,
    )
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert result.exit_code == 0
    assert count == 100_000
    assert len(result.error_tail) <= 50
    # ru_maxrss is KiB on Linux, bytes on macOS; either way 10M of text must not stay resident.
    assert after - before < 200 * 1024 * 1024


async def test_runner_plain_text_errors_reach_error_tail(fake_rclone, tmp_path):
    fake_rclone.on(
        "sync",
        stderr=[
            "Error: unknown flag: --bogus",
            "Usage:",
            "  rclone sync source:path dest:path [flags]",
        ],
        exit=1,
    )
    result = await run_rclone([fake_rclone.binary, "sync"], fake_rclone.env(), log_path=None)
    assert "Error: unknown flag: --bogus" in result.error_tail


async def test_runner_failed_to_notice_reaches_error_tail(fake_rclone):
    fake_rclone.on(
        "bisync", stderr=[jl("Failed to bisync: bisync aborted", level="notice")], exit=2
    )
    result = await run_rclone([fake_rclone.binary, "bisync"], fake_rclone.env(), log_path=None)
    assert result.error_tail == ["Failed to bisync: bisync aborted"]


async def test_runner_survives_a_two_mebibyte_line(fake_rclone, tmp_path):
    fake_rclone.on("bisync", stderr=["y" * (2 * 1024 * 1024), jl("after")])
    log = tmp_path / "big.log"
    seen = []
    result = await run_rclone(
        [fake_rclone.binary, "bisync"], fake_rclone.env(), log_path=log, on_line=seen.append
    )
    assert result.exit_code == 0
    assert "[truncated]" in log.read_text()
    assert log.stat().st_size < 1024 * 1024 + 1000
    assert seen[-1].msg == "after"


def _alive(marker: str) -> bool:
    return subprocess.run(["pgrep", "-f", marker], capture_output=True).returncode == 0


async def test_runner_cleans_up_process_when_callback_raises(fake_rclone, tmp_path):
    marker = f"--marker-{uuid.uuid4().hex}"
    fake_rclone.on("bisync", stderr=[jl("first")], sleep=60)

    def boom(_):
        raise RuntimeError("callback failed")

    start = time.monotonic()
    with pytest.raises(RuntimeError, match="callback failed"):
        await run_rclone(
            [fake_rclone.binary, "bisync", marker], fake_rclone.env(), log_path=None, on_line=boom
        )
    assert time.monotonic() - start < 20
    await asyncio.sleep(0.2)
    assert not _alive(marker)


async def test_runner_json_error_line_reaches_error_tail_and_classifies_auth(fake_rclone, tmp_path):
    from app.services.rclone.errors import Failure, classify_failure

    fake_rclone.on(
        "about",
        stderr=['{"level":"error","msg":"couldn\'t fetch token: invalid_grant"}'],
        exit=1,
    )
    log = tmp_path / "x.log"
    res = await run_rclone(
        [fake_rclone.binary, "about", "gd:"], fake_rclone.env(), log_path=log, secrets=["abcdef123"]
    )
    assert res.error_tail
    assert classify_failure(res.exit_code, res.error_tail) == Failure.AUTH


async def test_runner_masks_secrets_in_callback_and_tail(fake_rclone, tmp_path):
    fake_rclone.on("about", stderr=[jl("oops supersecretvalue", level="error")], exit=1)
    seen = []
    res = await run_rclone(
        [fake_rclone.binary, "about", "gd:"],
        fake_rclone.env(),
        log_path=tmp_path / "y.log",
        on_line=seen.append,
        secrets=["supersecretvalue"],
    )
    assert "supersecretvalue" not in " ".join(res.error_tail)
    assert all("supersecretvalue" not in ln.msg + ln.raw for ln in seen)
    assert "supersecretvalue" not in (tmp_path / "y.log").read_text()


async def test_runner_passes_unmasked_text_for_counting_but_masks_the_log(fake_rclone, tmp_path):
    from app.services.rclone.parse import Counters

    fake_rclone.on(
        "bisync",
        stderr=[jl("- Path2    Queue delete                        - {path2/}token=abc.txt")],
    )
    counters = Counters()
    seen = []

    def on_line(line):
        seen.append(line)
        counters.feed(line)

    log = tmp_path / "job.log"
    await run_rclone(
        [fake_rclone.binary, "bisync"], fake_rclone.env(), log_path=log, on_line=on_line
    )
    assert counters.planned == [("remote", "delete", "token=abc.txt")]
    assert "token=abc.txt" not in log.read_text()
    assert "token=abc.txt" not in seen[0].msg


async def test_runner_plain_text_failed_to_notice_reaches_error_tail(fake_rclone):
    # Commands run without --use-json-log (e.g. lsjson) print this as plain text.
    line = "2026/10/08 17:22:48 NOTICE: Failed to lsjson: directory not found"
    fake_rclone.on("lsjson", stderr=[line, "2026/10/08 17:22:48 NOTICE: all good"], exit=3)
    result = await run_rclone([fake_rclone.binary, "lsjson"], fake_rclone.env(), log_path=None)
    assert result.error_tail == [line]
