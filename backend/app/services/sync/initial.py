import asyncio
import json
import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.pathsafe import UnsafePath, safe_join
from app.models import Account, SyncPair
from app.models.enums import AttentionReason, Direction, JobKind, JobState, PairStatus
from app.services.events.bus import record_event
from app.services.rclone.commands import bisync_args, check_args, remote_target, touch_args
from app.services.rclone.errors import Failure, classify_failure
from app.services.rclone.parse import CheckSummary, parse_check_combined
from app.services.rclone.runner import RunResult
from app.services.sync.engine import SyncEngine
from app.services.sync.filters import refresh_pair_filters
from app.services.sync.pairs import pair_local_dir, pair_remote

log = logging.getLogger(__name__)

CONFIRM_PHRASE = "I understand and want to start synchronization."
_SAMPLE = 1000
_KEYS = {"=": "same", "+": "only_local", "-": "only_remote", "*": "different", "!": "errors"}


class InitialSync:
    def __init__(self, engine: SyncEngine) -> None:
        self.engine = engine
        engine.initial = self
        # Called with the pair id after a successful safe resync or first sync (Offload uses
        # it to finish a Restore to Drive).
        self.on_resync_done: list[Callable[[int], None]] = []

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
        only_local_all: list[str] = []
        only_remote_folded: set[str] = set()
        summary = CheckSummary()
        with open(different_out, "w", encoding="utf-8") as diff_fh:

            def on_stdout(line: str) -> None:
                parsed = parse_check_combined(line)
                if parsed is None:
                    return
                key = _KEYS[parsed[0]]
                counts[key] += 1
                if key in samples and len(samples[key]) < _SAMPLE:
                    samples[key].append(parsed[1])
                if key in ("different", "errors"):
                    diff_fh.write(parsed[1] + "\n")
                elif key == "only_local":
                    only_local_all.append(parsed[1])
                elif key == "only_remote":
                    only_remote_folded.add(parsed[1].casefold())

            res = await e.runner(
                check_args(
                    e.env,
                    pair_local_dir(e.layout, account, pair),
                    pair_remote(account, pair),
                    e.layout.pair_filters(pair.id),
                ),
                e.env.env(),
                log_path=Path(job.log_path),
                on_line=summary.feed,
                on_stdout=on_stdout,
                secrets=[e.env.config_pass],
            )
            # A local-only file whose name differs from a Drive-only file only by case can be
            # overwritten when the two sides are merged on a case-insensitive side.
            for rel in only_local_all:
                if rel.casefold() in only_remote_folded:
                    diff_fh.write(rel + "\n")
        # Exit 1 also covers fatal errors (e.g. the remote can't be opened): only accept it as
        # "differences found" when rclone's own summary matches the listing.
        unexplained = res.exit_code == 1 and not summary.explains(
            differences=counts["only_local"] + counts["only_remote"] + counts["different"],
            errors=counts["errors"],
        )
        if res.exit_code not in (0, 1) or unexplained:
            if unexplained:
                res = RunResult(
                    exit_code=res.exit_code,
                    duration_s=res.duration_s,
                    error_tail=[
                        *res.error_tail,
                        "rclone check reported problems besides file differences; "
                        "nothing was changed",
                    ],
                )
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
    @staticmethod
    def _copy_files(local_dir: Path, dest_root: Path, rels: list[str]) -> int:
        copied = 0
        for rel in rels:
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

    async def _backup(
        self, account: Account, pair: SyncPair, list_file: Path, label: str, job_id: int
    ) -> int:
        e = self.engine
        dest_root = (
            e.layout.backups_dir
            / account.email
            / str(pair.id)
            / e.clock().date().isoformat()
            / f".drivesync-{label}-{job_id}"
        )
        rels = list_file.read_text(encoding="utf-8").splitlines() if list_file.exists() else []
        local_dir = pair_local_dir(e.layout, account, pair)
        return await asyncio.to_thread(self._copy_files, local_dir, dest_root, rels)

    def _fail_pair(
        self, db: Session, job, pair: SyncPair, account: Account, message: str, reason: str
    ) -> int:
        e = self.engine
        e.finish_job(db, job, JobState.FAILED, error=message)
        pair.last_error = message
        pair.last_error_at = e.clock()
        e.set_status(db, pair, PairStatus.ATTENTION, reason)
        record_event(
            db,
            e.bus,
            kind="sync_failed",
            level="error",
            pair_id=pair.id,
            account_id=account.id,
            message=f"{pair.name}: {message}",
        )
        return job.id

    async def _resync(
        self,
        db: Session,
        pair: SyncPair,
        account: Account,
        trigger: str,
        result: dict,
        list_file: Path,
        label: str,
    ) -> int:
        e = self.engine
        if pair.direction != Direction.BIDIRECTIONAL:
            raise ValueError("A safe resync only applies to two-way pairs.")
        job = e.start_job(db, pair, JobKind.RESYNC, trigger)
        if result["errors"] > 0:
            return self._fail_pair(
                db,
                job,
                pair,
                account,
                f"rclone could not compare {result['errors']} files; nothing was changed",
                AttentionReason.FAILED,
            )
        try:
            copied = await self._backup(account, pair, list_file, label, job.id)
        except Exception as exc:
            return self._fail_pair(
                db,
                job,
                pair,
                account,
                f"Could not save backups of local files, so nothing was changed: {exc}",
                AttentionReason.FAILED,
            )
        with open(job.log_path, "a", encoding="utf-8") as fh:
            fh.write(f"CleanSync: saved {copied} local files to backups before the {label}\n")
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
            if label == "initial" and failure in (Failure.TRANSIENT, Failure.OTHER):
                pair.fail_count = 0
                pair.next_retry_at = None
                pair.last_error = (
                    f"{pair.last_error}. The first sync did not finish; run the preview "
                    "again before retrying."
                )
                db.commit()
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
        for listener in self.on_resync_done:
            try:
                listener(pair.id)
            except Exception:
                log.exception("a listener for finished resyncs failed (pair %s)", pair.id)
        return job.id

    async def run_initial(self, pair_id: int) -> int:
        if self.load_preview(pair_id) is None:
            raise ValueError("Run the preview first and review it before starting.")
        e = self.engine
        with e.session_factory() as db:
            pair = db.get(SyncPair, pair_id)
            account = db.get(Account, pair.account_id)
            fresh = e.layout.previews_dir / f"{pair_id}-initial-different.txt"
            try:
                # The stored preview only proves the user reviewed it; what could be
                # overwritten is decided by a check made right now.
                job_id, result = await self._check(db, pair, account, "INITIAL", fresh)
                if result is None:
                    return job_id
                if pair.direction != Direction.BIDIRECTIONAL:
                    # One-way pairs never run a resync; their first real run goes through the
                    # dry-run deletion gate.
                    pair.initialized = True
                    pair.needs_resync = False
                    pair.last_error = None
                    e.set_status(db, pair, PairStatus.HEALTHY)
                else:
                    job_id = await self._resync(
                        db, pair, account, "INITIAL", result, fresh, "initial"
                    )
                    db.refresh(pair)
                if pair.initialized:
                    for f in (self._preview_file(pair_id), self._different_file(pair_id)):
                        f.unlink(missing_ok=True)
                    fresh.unlink(missing_ok=True)
                return job_id
            except Exception as exc:
                e._mark_crashed(db, pair, account, exc)
                raise

    async def safe_resync(self, pair_id: int, trigger: str) -> int:
        e = self.engine
        with e.session_factory() as db:
            pair = db.get(SyncPair, pair_id)
            account = db.get(Account, pair.account_id)
            if pair.direction != Direction.BIDIRECTIONAL:
                raise ValueError("A safe resync only applies to two-way pairs.")
            diff = e.layout.previews_dir / f"{pair_id}-resync-different.txt"
            try:
                job_id, result = await self._check(db, pair, account, trigger, diff)
                if result is None:
                    return job_id
                return await self._resync(db, pair, account, trigger, result, diff, "resync")
            except Exception as exc:
                e._mark_crashed(db, pair, account, exc)
                raise
