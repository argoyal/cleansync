#!/usr/bin/env python3
"""Stand-in for rclone in tests. Rules come from $FAKE_RCLONE_PLAN (JSON), calls go to $FAKE_RCLONE_CALLS."""

import json
import os
import sys
import time
from pathlib import Path

GLOBAL_WITH_VALUE = {"--config", "--cache-dir"}


def subcommand(argv: list[str]) -> str:
    i = 0
    while i < len(argv):
        if argv[i] in GLOBAL_WITH_VALUE:
            i += 2
            continue
        return argv[i]
    return ""


def main() -> int:
    argv = sys.argv[1:]
    with open(os.environ["FAKE_RCLONE_CALLS"], "a") as fh:
        fh.write(json.dumps(argv) + "\n")
    plan = json.loads(Path(os.environ["FAKE_RCLONE_PLAN"]).read_text())
    sub = subcommand(argv)
    if sub == "version":
        print("rclone v1.99.0-fake")
        return 0
    for rule in plan.get(sub, []):
        if all(m in argv for m in rule.get("match", [])) and not any(
            m in argv for m in rule.get("not_match", [])
        ):
            for path in rule.get("create_files", []):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text("x")
            for line in rule.get("stdout", []):
                sys.stdout.write(line + "\n")
            for line in rule.get("stderr", []):
                sys.stderr.write(line + "\n")
            sys.stdout.flush()
            sys.stderr.flush()
            if rule.get("sleep"):  # after output, so tests can act on early lines mid-run
                time.sleep(rule["sleep"])
            return int(rule.get("exit", 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
