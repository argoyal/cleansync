import json

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


def test_counters_from_bisync_queue_lines():
    c = Counters()
    for msg in [
        "- Path2    Queue delete                        - {path2/}old.txt",
        "- Path1    Queue delete                        - {path1/}gone.pdf",
        "- Path1    Queue copy to Path2                 - {path2/}new.txt",
        "- Path2    Queue copy to Path1                 - {path1/}photo.jpg",
        "- WARNING  New or changed in both paths        - report.xlsx",
    ]:
        c.feed(parse_line(jl(msg)))
    assert (c.uploaded, c.downloaded, c.deleted_local, c.deleted_remote) == (1, 1, 1, 1)
    assert c.deletions == 2
    assert c.conflict_paths == ["report.xlsx"]
    assert ("remote", "delete", "old.txt") in c.planned
    assert ("local", "delete", "gone.pdf") in c.planned


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
