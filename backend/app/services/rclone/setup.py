from pathlib import Path

import app as app_pkg
from app.config import Settings
from app.paths import Layout
from app.secrets_store import resolve_secret
from app.services.rclone.commands import RcloneEnv, config_encrypt_args
from app.services.rclone.runner import run_rclone

_ENCRYPTED_HEADER = "# Encrypted rclone configuration File"
_DEFAULT_PASSCMD = Path(app_pkg.__file__).resolve().parents[2] / "docker" / "print-rclone-pass"


def build_rclone_env(settings: Settings, layout: Layout) -> RcloneEnv:
    config_pass = resolve_secret(
        settings.rclone_config_pass, layout.secrets_dir / "rclone_config_pass"
    )
    return RcloneEnv(
        binary=settings.rclone_binary,
        config_file=layout.rclone_conf,
        cache_dir=layout.rclone_cache,
        config_pass=config_pass,
        password_command=settings.rclone_password_command or str(_DEFAULT_PASSCMD),
    )


async def ensure_config_encrypted(env: RcloneEnv, runner=run_rclone) -> bool:
    conf = env.config_file
    if not conf.exists() or conf.read_text(errors="replace").startswith(_ENCRYPTED_HEADER):
        return False
    res = await runner(
        config_encrypt_args(env),
        env.env() | {"RCLONE_PASSWORD_CHANGE": "1"},
        log_path=None,
        secrets=[env.config_pass],
    )
    if res.exit_code != 0:
        raise RuntimeError("could not encrypt rclone.conf: " + "; ".join(res.error_tail))
    conf.chmod(0o600)
    return True
