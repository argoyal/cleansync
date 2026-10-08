import json

from app.core.pathsafe import normalize_rel, safe_join, validate_remote_path
from app.models import Account
from app.paths import Layout
from app.services.rclone.commands import RcloneEnv, lsjson_args, remote_target
from app.services.rclone.errors import Failure, classify_failure
from app.services.rclone.runner import run_rclone

_HIDDEN = {".google-exports"}


class BrowseError(Exception):
    def __init__(self, message: str, failure: Failure | None) -> None:
        super().__init__(message)
        self.failure = failure


async def list_remote_dirs(
    env: RcloneEnv, remote_name: str, path: str, runner=run_rclone
) -> list[dict]:
    clean = validate_remote_path(path)
    out: list[str] = []
    res = await runner(
        lsjson_args(env, remote_target(remote_name, clean), dirs_only=True),
        env.env(),
        log_path=None,
        on_stdout=out.append,
        secrets=[env.config_pass],
    )
    if res.exit_code != 0:
        failure = classify_failure(res.exit_code, res.error_tail)
        raise BrowseError("; ".join(res.error_tail) or "could not list the folder", failure)
    items = json.loads("\n".join(out) or "[]")
    prefix = f"{clean}/" if clean else ""
    return sorted(
        ({"name": i["Name"], "path": prefix + i["Name"]} for i in items if i["IsDir"]),
        key=lambda d: d["name"].lower(),
    )


def list_local_dirs(layout: Layout, account: Account, path: str) -> list[dict]:
    clean = normalize_rel(path)
    base = safe_join(layout.account_dir(account.email), clean)
    prefix = f"{clean}/" if clean else ""
    if not base.is_dir():
        return []
    return sorted(
        (
            {"name": p.name, "path": prefix + p.name}
            for p in base.iterdir()
            if p.is_dir() and p.name not in _HIDDEN
        ),
        key=lambda d: d["name"].lower(),
    )
