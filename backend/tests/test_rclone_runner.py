import resource

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
