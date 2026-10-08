import os
import time

from app.services.logs import prune_job_logs, tail_lines


def test_tail_reads_last_lines_of_big_file(tmp_path):
    f = tmp_path / "x.log"
    f.write_text("".join(f"line {i}\n" for i in range(200_000)))
    assert tail_lines(f, 3) == ["line 199997", "line 199998", "line 199999"]


def test_tail_of_short_and_missing_files(tmp_path):
    f = tmp_path / "s.log"
    f.write_text("a\nb\n")
    assert tail_lines(f, 10) == ["a", "b"]
    assert tail_lines(tmp_path / "missing.log", 10) == []


def test_prune_old_job_logs(layout):
    old = layout.job_log(1)
    new = layout.job_log(2)
    old.write_text("x")
    new.write_text("y")
    long_ago = time.time() - 40 * 86400
    os.utime(old, (long_ago, long_ago))
    assert prune_job_logs(layout, 30, time.time()) == 1
    assert not old.exists() and new.exists()
