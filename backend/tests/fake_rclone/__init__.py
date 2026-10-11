import json
import os
import stat
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parent / "fake_rclone.py"


def jl(msg: str, level: str = "info", **extra) -> str:
    return json.dumps(
        {"time": "2026-10-08T08:00:00Z", "level": level, "msg": msg, "source": "fake"} | extra
    )


class FakeRclone:
    def __init__(self, workdir: Path) -> None:
        workdir.mkdir(parents=True, exist_ok=True)
        self.plan_file = workdir / "plan.json"
        self.calls_file = workdir / "calls.jsonl"
        self.plan_file.write_text("{}")
        self.calls_file.write_text("")
        wrapper = workdir / "rclone"
        # The wrapper carries its own settings, because CleanSync runs rclone with a minimal env.
        wrapper.write_text(
            "#!/bin/sh\n"
            f'export FAKE_RCLONE_PLAN="{self.plan_file}"\n'
            f'export FAKE_RCLONE_CALLS="{self.calls_file}"\n'
            f'exec "{sys.executable}" "{_SCRIPT}" "$@"\n'
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
        self.binary = str(wrapper)

    def on(
        self,
        subcommand: str,
        *,
        stderr=(),
        stdout=(),
        exit: int = 0,
        match=(),
        not_match=(),
        create_files=(),
        sleep: float = 0,
    ) -> None:
        plan = json.loads(self.plan_file.read_text())
        plan.setdefault(subcommand, []).append(
            {
                "stderr": list(stderr),
                "stdout": list(stdout),
                "exit": exit,
                "match": list(match),
                "not_match": list(not_match),
                "create_files": [str(p) for p in create_files],
                "sleep": sleep,
            }
        )
        self.plan_file.write_text(json.dumps(plan))

    def calls(self) -> list[list[str]]:
        return [json.loads(x) for x in self.calls_file.read_text().splitlines() if x]

    def env(self) -> dict[str, str]:
        return {"PATH": os.environ["PATH"]}


def check_out(lines: list[str], *, extra_errors: int = 0) -> dict:
    """Fake `rclone check --combined -` output: the lines plus rclone's closing summary."""
    diffs = sum(1 for ln in lines if ln[:1] in "+-*")
    errors = diffs + sum(1 for ln in lines if ln[:1] == "!") + extra_errors
    stderr = [jl(f"{diffs} differences found", level="notice", object="drive")]
    if errors:
        stderr.append(jl(f"{errors} errors while checking", level="notice", object="drive"))
    return {"stdout": list(lines), "stderr": stderr, "exit": 1 if errors else 0}
