import json
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.pathsafe import UnsafePath, safe_join
from app.models import Account, SyncPair
from app.models.enums import JobKind, JobState, PairStatus
from app.services.events.bus import record_event
from app.services.rclone.commands import bisync_args, check_args, remote_target, touch_args
from app.services.rclone.errors import classify_failure
from app.services.rclone.parse import parse_check_combined
from app.services.sync.engine import SyncEngine
from app.services.sync.filters import refresh_pair_filters
from app.services.sync.pairs import pair_local_dir, pair_remote

CONFIRM_PHRASE = "I understand and want to start synchronization."
_SAMPLE = 1000
_KEYS = {"=": "same", "+": "only_local", "-": "only_remote", "*": "different", "!": "errors"}


class InitialSync:
    def __init__(self, engine: SyncEngine) -> None:
        self.engine = engine
        engine.initial = self

    # ---- files ----------------------------------------------------------------------------
    def _preview_file(self, pair_id: int) -> Path:
        return self.engine.layout.previews_dir / f"{pair_id}.json"

    def _different_file(self, pair_id: int) -> Path:
        return self.engine.layout.previews_dir / f"{pair_id}-different.txt"

    def load_preview(self, pair_id: int) -> dict | None:
        f = self._preview_file(pair_id)
        return json.loads(f.read_text()) if f.exists() else None

    def check_phrase(self, phrase: str) -> None:
        if phrase != CONFIRM_PHRASE:
            raise ValueError(f'Type exactly: "{CONFIRM_PHRASE}"')

    # ---- check ------------------------------------------------------------------------------
    async def _check(
        self, db: Session, pair: SyncPair, account: Account, trigger: str, different_out: Path
    ) -> tuple[int, dict | None]:
        e = self.engine
        refresh_pair_filters(db, e.layout, pair)
        job = e.start_job(db, pair, JobKind.PREVIEW, trigger)
        counts = dict.fromkeys(_KEYS.values(), 0)
        samples: dict[str, list[str]] = {
            k: [] for k in ("only_local", "only_remote", "different", "errors")
        }
        different_out.parent.mkdir(parents=True, exist_ok=True)
        with open(different_out, "w", encoding="utf-8") as diff_fh:

            def on_stdout(line: str) -> None:
                parsed = parse_check_combined(line)
                if parsed is None:
                    return
                key = _KEYS[parsed[0]]
                counts[key] += 1
                if key in samples and len(samples[key]) < _SAMPLE:
                    samples[key].append(parsed[1])
                if key == "different":
                    diff_fh.write(parsed[1] + "\n")

            res = await e.runner(
                check_args(
                    e.env,
                    pair_local_dir(e.layout, account, pair),
                    pair_remote(account, pair),
                    e.layout.pair_filters(pair.id),
                ),
                e.env.env(),
                log_path=Path(job.log_path),
                on_stdout=on_stdout,
                secrets=[e.env.config_pass],
            )
        if res.exit_code not in (0, 1):
            e.finish_job(db, job, JobState.FAILED, res)
            failure = classify_failure(res.exit_code, res.error_tail)
            if failure is not None:
                await e.handle_failure(db, pair, account, failure, res)
            return job.id, None
        e.finish_job(db, job, JobState.SUCCESS, res)
        return job.id, {
            "job_id": job.id,
            "created_at": e.clock().isoformat(),
            **counts,
            "samples": samples,
        }

    async def preview(self, pair_id: int) -> int:
        e = self.engine
        with e.session_factory() as db:
            pair = db.get(SyncPair, pair_id)
            account = db.get(Account, pair.account_id)
            try:
                job_id, result = await self._check(
                    db, pair, account, "INITIAL", self._different_file(pair_id)
                )
            except Exception as exc:
                e._mark_crashed(db, pair, account, exc)
                raise
            if result is not None:
                self._preview_file(pair_id).write_text(json.dumps(result))
                record_event(
                    db,
                    e.bus,
                    kind="preview_ready",
                    pair_id=pair.id,
                    account_id=account.id,
                    message=f"{pair.name}: first-sync preview is ready to review",
                )
            return job_id

    # ---- resync -----------------------------------------------------------------------------
    def _backup_different(
        self, account: Account, pair: SyncPair, different_file: Path, label: str
    ) -> int:
        e = self.engine
        local_dir = pair_local_dir(e.layout, account, pair)
        dest_root = (
            e.layout.backups_dir
            / account.email
            / str(pair.id)
            / e.clock().date().isoformat()
            / label
        )
        copied = 0
        if not different_file.exists():
            return 0
        for rel in different_file.read_text(encoding="utf-8").splitlines():
            try:
                src = safe_join(local_dir, rel)
                dst = safe_join(dest_root, rel)
            except UnsafePath:
                continue
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1
        return copied

    async def _resync(
        self,
        db: Session,
        pair: SyncPair,
        account: Account,
        trigger: str,
        different_file: Path,
        label: str,
    ) -> int:
        e = self.engine
        copied = self._backup_different(account, pair, different_file, label)
        job = e.start_job(db, pair, JobKind.RESYNC, trigger)
        e.set_status(db, pair, PairStatus.RUNNING)
        local_dir = pair_local_dir(e.layout, account, pair)
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "RCLONE_TEST").touch()
        marker = remote_target(
            account.remote_name,
            f"{pair.remote_path}/RCLONE_TEST" if pair.remote_path else "RCLONE_TEST",
        )
        res = await e.run(touch_args(e.env, marker), job)
        if res.exit_code == 0:
            res = await e.run(
                bisync_args(e.env, e.bisync_target(account, pair), dry_run=False, resync=True), job
            )
        failure = classify_failure(res.exit_code, res.error_tail)
        if failure is not None:
            e.finish_job(db, job, JobState.FAILED, res)
            await e.handle_failure(db, pair, account, failure, res)
            return job.id
        e.finish_job(db, job, JobState.SUCCESS, res)
        pair.initialized = True
        pair.needs_resync = False
        pair.last_success_at = e.clock()
        pair.last_sync_at = e.clock()
        pair.last_error = None
        pair.fail_count = 0
        e.set_status(db, pair, PairStatus.HEALTHY)
        record_event(
            db,
            e.bus,
            kind="resync_done",
            pair_id=pair.id,
            account_id=account.id,
            message=(
                f"{pair.name}: safe merge finished (nothing deleted); "
                f"{copied} local versions saved to backups first"
            ),
        )
        return job.id

    async def run_initial(self, pair_id: int) -> int:
        if self.load_preview(pair_id) is None:
            raise ValueError("Run the preview first and review it before starting.")
        e = self.engine
        with e.session_factory() as db:
            pair = db.get(SyncPair, pair_id)
            account = db.get(Account, pair.account_id)
            try:
                return await self._resync(
                    db, pair, account, "INITIAL", self._different_file(pair_id), "initial"
                )
            except Exception as exc:
                e._mark_crashed(db, pair, account, exc)
                raise

    async def safe_resync(self, pair_id: int, trigger: str) -> int:
        e = self.engine
        with e.session_factory() as db:
            pair = db.get(SyncPair, pair_id)
            account = db.get(Account, pair.account_id)
            diff = e.layout.previews_dir / f"{pair_id}-resync-different.txt"
            try:
                job_id, result = await self._check(db, pair, account, trigger, diff)
                if result is None:
                    return job_id
                return await self._resync(db, pair, account, trigger, diff, "resync")
            except Exception as exc:
                e._mark_crashed(db, pair, account, exc)
                raise
