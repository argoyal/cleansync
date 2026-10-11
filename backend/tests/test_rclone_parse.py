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
    line = parse_line(
        jl(
            "Skipped delete as --dry-run is set (size 3)",
            level="notice",
            skipped="delete",
            object="a.txt",
        )
    )
    assert line.skipped == "delete"
    assert count_dry_run_deletes(line)
    copy = parse_line(jl("Skipped copy as --dry-run is set", skipped="copy"))
    assert not count_dry_run_deletes(copy)
    # Counted from the structured field, not the wording of the message.
    assert not count_dry_run_deletes(parse_line(jl("Skipped delete as --dry-run is set")))


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


def test_counters_track_roots_success_and_final_stats():
    c = Counters()
    assert not c.has_roots and not c.bisync_successful and c.final_stats is None
    c.feed(parse_line(jl('Synching Path1 "/l/p1/" with Path2 "gd:Docs/"')))
    c.feed(parse_line(jl("x", stats={"deletes": 1})))
    c.feed(parse_line(jl("Bisync successful")))
    c.feed(parse_line(jl("final", stats={"deletes": 3})))
    assert c.has_roots and c.bisync_successful and c.final_stats == {"deletes": 3}


def test_counters_read_conflict_variant_names_from_rename_lines():
    c = Counters()
    c.feed(parse_line(jl('Synching Path1 "/l/p1/" with Path2 "gd:Docs/"')))
    c.feed(parse_line(jl("- WARNING  New or changed in both paths       - sub/both.txt")))
    c.feed(
        parse_line(
            jl("- Path1    Renaming Path1 copy                - /l/p1/sub/both.conflict3.txt")
        )
    )
    c.feed(
        parse_line(
            jl("- Path2    Renaming Path2 copy                - gd:Docs/sub/both.conflict4.txt")
        )
    )
    assert c.conflict_paths == ["sub/both.txt"]
    assert c.conflict_variants == {
        "sub/both.txt": ("sub/both.conflict3.txt", "sub/both.conflict4.txt")
    }


@pytest.mark.parametrize(
    "msg",
    [
        'too many deletes (>50%, 5 of 7) on Path1 "/l/". Run with --force if desired.',
        "Failed to bisync: Too Many Deletes",
        "Got fatal error on delete: --max-delete threshold reached",
    ],
)
def test_delete_brakes_are_their_own_failure_class(msg):
    assert classify_failure(1, [msg]) == Failure.TOO_MANY_DELETES
    assert classify_failure(7, [msg]) == Failure.TOO_MANY_DELETES


def test_same_change_on_both_sides_is_not_a_conflict():
    # rclone warns "New or changed in both paths", then finds both sides equal (or a folder)
    # and leaves them alone: e.g. after CleanSync renamed a folder on both sides.
    c = Counters()
    c.feed(parse_line(jl('Synching Path1 "/l/p1/" with Path2 "gd:Docs/"')))
    for path in ("New", "New/a.txt", "both.txt"):
        c.feed(parse_line(jl(f"- WARNING  New or changed in both paths       - {path}")))
        if path == "New":
            c.feed(
                parse_line(
                    jl(
                        "This is a directory, not a file. Skipping equality check and will not "
                        "rename: New"
                    )
                )
            )
        elif path == "New/a.txt":
            c.feed(parse_line(jl("Files are equal! Skipping: New/a.txt")))
    c.feed(
        parse_line(jl("- Path1    Renaming Path1 copy                - /l/p1/both.conflict1.txt"))
    )
    c.feed(
        parse_line(jl("- Path2    Renaming Path2 copy                - gd:Docs/both.conflict2.txt"))
    )
    assert c.conflict_paths == ["both.txt"]
    assert c.conflict_variants == {"both.txt": ("both.conflict1.txt", "both.conflict2.txt")}


def test_equal_files_with_different_times_are_not_a_conflict():
    # Real rclone 1.7x sequence: the warning, then this INFO line with the path as its object
    # (JSON log), then a queued copy that only updates the modification time.
    c = Counters()
    c.feed(parse_line(jl('Synching Path1 "/l/p1/" with Path2 "gd:Docs/"')))
    c.feed(parse_line(jl("- WARNING  New or changed in both paths       - s.txt", level="notice")))
    c.feed(
        parse_line(
            jl(
                "Files are equal but will copy anyway to update modtime (will not rename)",
                object="s.txt",
                objectType="string",
            )
        )
    )
    c.feed(parse_line(jl("- Path1    Queue copy to Path2       - gd:Docs/s.txt", level="notice")))
    assert c.conflict_paths == []
    # Plain text form: "s.txt: Files are equal but will copy anyway ..."
    c = Counters()
    c.feed(parse_line(jl("- WARNING  New or changed in both paths       - t.txt")))
    c.feed(
        parse_line(
            "2026/10/08 17:36:18 INFO  : t.txt: Files are equal but will copy anyway to update "
            "modtime (will not rename)"
        )
    )
    assert c.conflict_paths == []
