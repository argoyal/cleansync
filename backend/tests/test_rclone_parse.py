import json

import pytest

from app.services.rclone.errors import Failure, classify_failure
from app.services.rclone.parse import (
    Counters,
    count_dry_run_deletes,
    parse_check_combined,
    parse_line,
    progress_from_stats,
)
from tests.fake_rclone import jl


def test_parse_json_line():
    line = parse_line(jl("hello", level="error", object="a/b.txt"))
    assert (line.level, line.msg, line.obj) == ("error", "hello", "a/b.txt")


def test_parse_plain_text_line():
    line = parse_line("2026/10/08 08:00:00 ERROR : something broke")
    assert line.level == "error"
    assert "something broke" in line.msg


def test_progress_from_stats_line():
    stats = {
        "bytes": 50,
        "totalBytes": 200,
        "speed": 10.5,
        "eta": 15,
        "transferring": [{"name": "video.mp4", "bytes": 50, "size": 200, "percentage": 25}],
    }
    line = parse_line(json.dumps({"level": "notice", "msg": "stats", "stats": stats}))
    p = progress_from_stats(line.stats)
    assert (p.bytes, p.total_bytes, p.speed, p.eta) == (50, 200, 10.5, 15)
    assert p.transferring[0]["name"] == "video.mp4"


_ROOTS_LINE = 'Synching Path1 "/srv/drivesync/storage/alice@example.com/" with Path2 "gd_1:Docs/"'
_L = "/srv/drivesync/storage/alice@example.com/"
_R = "gd_1:Docs/"


def _real_lines(colour: bool) -> list[str]:
    def c(label: str, code: str) -> str:
        return f"\x1b[{code}m{label}\x1b[0m" if colour else label

    def q(side: str, action: str, path: str) -> str:
        return f"- {c(side, '36')}    {c(action, '35')}                 - {c(path, '36')}"

    return [
        _ROOTS_LINE,
        q("Path2", "Queue delete", _R + "sub dir/old file.txt"),
        q("Path1", "Queue delete", _L + "gone.pdf"),
        q("Path1", "Queue copy to Path2", _R + "new.txt"),
        q("Path2", "Queue copy to Path1", _L + "my folder/photo 1.jpg"),
        f"- {c('WARNING', '34')}  {c('New or changed in both paths', '35')}       - "
        f"{c('sub dir/report.xlsx', '36')}",
    ]


@pytest.mark.parametrize("colour", [False, True])
def test_counters_from_real_format_bisync_lines(colour):
    c = Counters()
    for msg in _real_lines(colour):
        c.feed(parse_line(jl(msg, level="notice")))
    assert (c.uploaded, c.downloaded, c.deleted_local, c.deleted_remote) == (1, 1, 1, 1)
    assert c.deletions == 2
    assert c.conflict_paths == ["sub dir/report.xlsx"]
    assert c.planned == [
        ("remote", "delete", "sub dir/old file.txt"),
        ("local", "delete", "gone.pdf"),
        ("remote", "copy", "new.txt"),
        ("local", "copy", "my folder/photo 1.jpg"),
    ]


def test_counters_still_accept_path_prefix_form():
    c = Counters()
    c.feed(parse_line(jl("- Path2    Queue delete                        - {path2/}old.txt")))
    assert c.planned == [("remote", "delete", "old.txt")]


def test_one_way_dry_run_delete_lines():
    assert count_dry_run_deletes(
        parse_line(
            jl("Skipped delete as --dry-run is set (size 3)", level="notice", object="a.txt")
        )
    )
    assert not count_dry_run_deletes(parse_line(jl("Skipped copy as --dry-run is set")))


def test_check_combined_lines():
    assert parse_check_combined("= same.txt") == ("=", "same.txt")
    assert parse_check_combined("+ only local/a b.txt") == ("+", "only local/a b.txt")
    assert parse_check_combined("- only drive.pdf") == ("-", "only drive.pdf")
    assert parse_check_combined("* differ.xlsx") == ("*", "differ.xlsx")
    assert parse_check_combined("garbage") is None


def test_failure_classes():
    assert classify_failure(0, []) is None
    assert (
        classify_failure(1, ['oauth2: "invalid_grant" "Token has been expired or revoked."'])
        == Failure.AUTH
    )
    assert (
        classify_failure(2, ["Bisync critical error: check file check failed"])
        == Failure.ACCESS_CHECK
    )
    assert (
        classify_failure(2, ["Bisync aborted. Must run --resync to recover."])
        == Failure.NEEDS_RESYNC
    )
    assert classify_failure(5, ["whatever"]) == Failure.TRANSIENT
    assert (
        classify_failure(1, ["googleapi: Error 403: User Rate Limit Exceeded, rateLimitExceeded"])
        == Failure.TRANSIENT
    )
    assert classify_failure(1, ["some other problem"]) == Failure.OTHER


def test_ansi_colour_codes_are_stripped_from_real_bisync_lines():
    # Real rclone 1.74 output: colour codes sit inside the JSON msg, and local-to-local runs
    # print absolute paths instead of {pathN/} prefixes.
    c = Counters()
    for msg in [
        'Synching Path1 "/t/p1/" with Path2 "/t/p2/"',
        "- \x1b[34mWARNING\x1b[0m  \x1b[35mNew or changed in both paths\x1b[0m       - "
        "\x1b[36mboth.txt\x1b[0m",
        "- \x1b[34mPath2\x1b[0m    \x1b[35m\x1b[31mQueue delete\x1b[0m\x1b[0m              - "
        "\x1b[36m/t/p2/keep.txt\x1b[0m",
    ]:
        c.feed(parse_line(jl(msg)))
    assert c.conflict_paths == ["both.txt"]
    assert c.planned == [("remote", "delete", "keep.txt")]


def test_config_errors_win_over_needs_resync():
    resync = "Bisync aborted. Must run --resync to recover."
    assert classify_failure(2, ["Error: unknown flag: --bogus", resync]) == Failure.OTHER
    assert (
        classify_failure(2, ["specified filters file does not exist: /x", resync]) == Failure.OTHER
    )
    assert classify_failure(2, ["unknown flag: --x", resync]) == Failure.OTHER


def test_counters_prefer_unmasked_text():
    from app.services.rclone.parse import LogLine

    c = Counters()
    c.feed(
        LogLine(
            level="info",
            msg="- Path2    Queue delete    - {path2/}token=***",
            obj=None,
            stats=None,
            raw="",
            plain_msg="- Path2    Queue delete    - {path2/}token=abc.txt",
        )
    )
    assert c.planned == [("remote", "delete", "token=abc.txt")]
