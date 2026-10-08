import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import utcnow
from app.models import Account, Conflict, PlannedChange, SyncJob, SyncPair
from app.models.enums import (
    AccountStatus,
    AttentionReason,
    ConflictState,
    Direction,
    JobKind,
    JobState,
    PairStatus,
    Trigger,
)
from app.paths import Layout
from app.services.events.bus import EventBus, record_event
from app.services.rclone.commands import BisyncTarget, RcloneEnv, bisync_args, sync_args
from app.services.rclone.errors import Failure, classify_failure
from app.services.rclone.parse import (
    Counters,
    LogLine,
    count_dry_run_deletes,
    progress_from_stats,
)
from app.services.rclone.runner import RunResult, run_rclone
from app.services.sync.conflicts import record_conflicts
from app.services.sync.filters import refresh_pair_filters
from app.services.sync.pairs import pair_local_dir, pair_remote

log = logging.getLogger(__name__)
_MAX_PLANNED_ROWS = 10_000
PLAN_UNREADABLE_MSG = "Couldn't read rclone's plan; nothing was changed"
# Pairs waiting on the user are not retried by timers or file changes, only by explicit actions.
_WAITING_FOR_USER = {
    AttentionReason.BLOCKED,
    AttentionReason.ACCESS_CHECK,
    AttentionReason.NEEDS_RESYNC,
}
_AUTOMATIC_TRIGGERS = {Trigger.SCHEDULE, Trigger.WATCHER}


def backoff(fail_count: int) -> timedelta:
    return timedelta(minutes=min(2 ** max(fail_count, 1), 60))


def is_eligible(pair: SyncPair, account: Account) -> bool:
    return (
        pair.enabled
        and not pair.paused
        and pair.initialized
        and account.status == AccountStatus.CONNECTED
    )


class SyncEngine:
    def __init__(
        self,
        *,
        layout: Layout,
        session_factory: sessionmaker[Session],
        bus: EventBus,
        rclone_env: RcloneEnv,
        accounts,
        runner=run_rclone,
        clock=utcnow,
    ) -> None:
        self.layout = layout
        self.session_factory = session_factory
        self.bus = bus
        self.env = rclone_env
        self.accounts = accounts
        self.runner = runner
        self.clock = clock
        self.initial = None  # set by InitialSync (Task 12)

    # ---- helpers reused by other services -------------------------------------------------
    def start_job(self, db: Session, pair: SyncPair, kind: str, trigger: str) -> SyncJob:
        job = SyncJob(
            pair_id=pair.id,
            kind=kind,
            trigger=trigger,
            state=JobState.RUNNING,
            started_at=self.clock(),
        )
        db.add(job)
        db.commit()
        job.log_path = str(self.layout.job_log(job.id))
        db.commit()
        self.bus.publish(
            "job", {"pair_id": pair.id, "job_id": job.id, "kind": kind, "state": job.state}
        )
        return job

    def finish_job(
        self,
        db: Session,
        job: SyncJob,
        state: str,
        result: RunResult | None = None,
        error: str | None = None,
    ) -> None:
        job.state = state
        job.completed_at = self.clock()
        if result is not None:
            job.exit_code = result.exit_code
            job.duration_s = round(result.duration_s, 2)
            if state == JobState.FAILED and error is None:
                error = (
                    "; ".join(result.error_tail[-5:]) or f"rclone exited with {result.exit_code}"
                )
        job.error_message = error
        db.commit()
        self.bus.publish(
            "job", {"pair_id": job.pair_id, "job_id": job.id, "kind": job.kind, "state": state}
        )

    def set_status(
        self, db: Session, pair: SyncPair, status: str, reason: str | None = None
    ) -> None:
        pair.status = status
        pair.attention_reason = reason
        db.commit()
        self.bus.publish("pair", {"pair_id": pair.id, "status": status, "reason": reason})

    def bisync_target(self, account: Account, pair: SyncPair) -> BisyncTarget:
        backup = None
        if pair.backups_enabled:
            backup = (
                self.layout.backups_dir
                / account.email
                / str(pair.id)
                / self.clock().date().isoformat()
            )
        return BisyncTarget(
            local=pair_local_dir(self.layout, account, pair),
            remote=pair_remote(account, pair),
            workdir=self.layout.pair_workdir(pair.id),
            filters_file=self.layout.pair_filters(pair.id),
            backup_dir=backup,
        )

    async def run(self, args: list[str], job: SyncJob, on_line=None) -> RunResult:
        return await self.runner(
            args,
            self.env.env(),
            log_path=Path(job.log_path),
            on_line=on_line,
            secrets=[self.env.config_pass],
        )

    async def handle_failure(
        self, db: Session, pair: SyncPair, account: Account, failure: Failure, result: RunResult
    ) -> None:
        now = self.clock()
        pair.last_error_at = now
        detail = "; ".join(result.error_tail[-3:])
        if failure == Failure.AUTH:
            pair.last_error = "Google login needs to be reconnected."
            self.set_status(db, pair, PairStatus.ATTENTION, AttentionReason.AUTH)
            await self.accounts.mark_needs_reauth(account.id, "the login expired or was revoked")
        elif failure == Failure.ACCESS_CHECK:
            pair.last_error = (
                "Marker file RCLONE_TEST is missing on one side. Is the storage mounted? "
                "Nothing was changed."
            )
            self.set_status(db, pair, PairStatus.ERROR, AttentionReason.ACCESS_CHECK)
        elif failure == Failure.NEEDS_RESYNC:
            pair.last_error = (
                "rclone needs a safe resync for this pair before it can continue. "
                "Use 'Run safe resync'."
            )
            self.set_status(db, pair, PairStatus.ATTENTION, AttentionReason.NEEDS_RESYNC)
        elif failure == Failure.TOO_MANY_DELETES:
            # A brake, not a glitch: no retry backoff, the user has to look first.
            pair.last_error = (
                "rclone's safety brake stopped this sync because it would delete too many "
                "files (bisync stops when more than 50% of the files on one side would be "
                "deleted). Check both folders, then run the sync again yourself."
            )
            self.set_status(db, pair, PairStatus.ATTENTION, AttentionReason.BLOCKED)
        else:
            pair.fail_count += 1
            pair.next_retry_at = now + backoff(pair.fail_count)
            pair.last_error = detail or f"rclone exited with {result.exit_code}"
            self.set_status(db, pair, PairStatus.ERROR, AttentionReason.FAILED)
        record_event(
            db,
            self.bus,
            kind="sync_failed",
            level="error",
            pair_id=pair.id,
            account_id=account.id,
            message=f"{pair.name}: {pair.last_error}",
        )

    # ---- the cycle -------------------------------------------------------------------------
    async def run_pair(self, pair_id: int, trigger: str) -> int | None:
        with self.session_factory() as db:
            pair = db.get(SyncPair, pair_id)
            if pair is None:
                return None
            account = db.get(Account, pair.account_id)
            if not is_eligible(pair, account):
                return None
            if trigger in _AUTOMATIC_TRIGGERS and pair.attention_reason in _WAITING_FOR_USER:
                return None
            if refresh_pair_filters(db, self.layout, pair):
                pair.needs_resync = True
                db.commit()
            if pair.needs_resync and self.initial is not None:
                return await self.initial.safe_resync(pair_id, trigger)
            bidirectional = pair.direction == Direction.BIDIRECTIONAL
            try:
                return await self._cycle(db, pair, account, trigger, bidirectional=bidirectional)
            except Exception as e:
                self._mark_crashed(db, pair, account, e)
                raise

    def _mark_crashed(self, db: Session, pair: SyncPair, account: Account, exc: Exception) -> None:
        """An unexpected error (e.g. rclone missing) must not leave the pair or a job RUNNING."""
        log.exception("sync of pair %s failed unexpectedly", pair.id)
        db.rollback()
        message = f"Sync stopped unexpectedly: {exc}"
        for job in db.scalars(
            select(SyncJob).where(SyncJob.pair_id == pair.id, SyncJob.state == JobState.RUNNING)
        ):
            self.finish_job(db, job, JobState.FAILED, error=message)
        pair.last_error = message
        pair.last_error_at = self.clock()
        self.set_status(db, pair, PairStatus.ERROR, AttentionReason.FAILED)
        record_event(
            db,
            self.bus,
            kind="sync_failed",
            level="error",
            pair_id=pair.id,
            account_id=account.id,
            message=f"{pair.name}: {message}",
        )

    def _args(
        self,
        account: Account,
        pair: SyncPair,
        *,
        dry_run: bool,
        bidirectional: bool,
        max_delete: int | None = None,
    ) -> list[str]:
        if bidirectional:
            return bisync_args(self.env, self.bisync_target(account, pair), dry_run=dry_run)
        local = str(pair_local_dir(self.layout, account, pair))
        remote = pair_remote(account, pair)
        filters = self.layout.pair_filters(pair.id)
        if pair.direction == Direction.REMOTE_TO_LOCAL:
            backup = self.bisync_target(account, pair).backup_dir
            return sync_args(
                self.env, remote, local, filters, backup, dry_run=dry_run, max_delete=max_delete
            )
        return sync_args(
            self.env, local, remote, filters, None, dry_run=dry_run, max_delete=max_delete
        )

    def _approved_deletions(self, db: Session, pair: SyncPair, current_job_id: int) -> int | None:
        """planned_deletions of the plan the user last reviewed (latest BLOCKED dry run)."""
        return db.scalar(
            select(SyncJob.planned_deletions)
            .where(
                SyncJob.pair_id == pair.id,
                SyncJob.kind == JobKind.DRY_RUN,
                SyncJob.state == JobState.BLOCKED,
                SyncJob.id != current_job_id,
            )
            .order_by(SyncJob.id.desc())
            .limit(1)
        )

    def _block(
        self, db: Session, pair: SyncPair, account: Account, dry: SyncJob, planned, res, n: int
    ) -> int:
        # Deletes first, so a capped list still shows every delete it can.
        rows = sorted(planned, key=lambda r: r[1] != "delete")[:_MAX_PLANNED_ROWS]
        for side, action, path in rows:
            db.add(PlannedChange(job_id=dry.id, side=side, action=action, path=path))
        self.finish_job(db, dry, JobState.BLOCKED, res)
        pair.last_error = (
            f"This sync would delete {n} files. Your limit is "
            f"{pair.max_deletions}. Review the changes before continuing."
        )
        pair.last_error_at = self.clock()
        self.set_status(db, pair, PairStatus.ATTENTION, AttentionReason.BLOCKED)
        record_event(
            db,
            self.bus,
            kind="sync_blocked",
            level="warning",
            pair_id=pair.id,
            account_id=account.id,
            message=f"{pair.name}: {pair.last_error}",
        )
        return dry.id

    def _plan_unreadable(
        self, db: Session, pair: SyncPair, account: Account, dry: SyncJob, res
    ) -> int:
        self.finish_job(db, dry, JobState.FAILED, res, error=PLAN_UNREADABLE_MSG)
        pair.last_error = PLAN_UNREADABLE_MSG
        pair.last_error_at = self.clock()
        self.set_status(db, pair, PairStatus.ATTENTION, AttentionReason.PLAN_UNREADABLE)
        record_event(
            db,
            self.bus,
            kind="sync_failed",
            level="error",
            pair_id=pair.id,
            account_id=account.id,
            message=f"{pair.name}: {PLAN_UNREADABLE_MSG}",
        )
        return dry.id

    def _one_way_side(self, pair: SyncPair) -> str:
        return "local" if pair.direction == Direction.REMOTE_TO_LOCAL else "remote"

    async def _cycle(
        self, db: Session, pair: SyncPair, account: Account, trigger: str, *, bidirectional: bool
    ) -> int:
        self.set_status(db, pair, PairStatus.RUNNING)
        pair.last_sync_at = self.clock()
        db.commit()

        # 1. dry run
        dry = self.start_job(db, pair, JobKind.DRY_RUN, trigger)
        planned = Counters()
        one_way_deletes: list[str] = []

        def on_dry(line: LogLine) -> None:
            planned.feed(line)
            if not bidirectional and count_dry_run_deletes(line):
                one_way_deletes.append(line.path_obj or "")

        res = await self.run(
            self._args(account, pair, dry_run=True, bidirectional=bidirectional), dry, on_dry
        )
        failure = classify_failure(res.exit_code, res.error_tail)
        if failure is not None:
            self.finish_job(db, dry, JobState.FAILED, res)
            await self.handle_failure(db, pair, account, failure, res)
            return dry.id
        # Fail closed: only trust a plan whose output we fully recognised.
        stats = planned.final_stats
        readable = stats is not None and (
            not bidirectional or (planned.has_roots and planned.bisync_successful)
        )
        if not readable:
            return self._plan_unreadable(db, pair, account, dry, res)
        if not bidirectional:
            side = self._one_way_side(pair)
            planned.planned += [(side, "delete", p) for p in one_way_deletes]
        parsed = planned.deletions if bidirectional else len(one_way_deletes)
        try:
            stat_deletes = int(stats.get("deletes") or 0)
        except (TypeError, ValueError):
            return self._plan_unreadable(db, pair, account, dry, res)
        deletions = max(parsed, stat_deletes)
        dry.planned_deletions = deletions

        # 2. deletion gate
        over = deletions > pair.max_deletions
        override_used = False
        if over and pair.override_next:
            # The override approves the plan the user reviewed, not whatever comes next.
            pair.override_next = False
            approved = self._approved_deletions(db, pair, dry.id)
            if approved is not None and deletions <= approved:
                override_used = True
            else:
                db.commit()
                record_event(
                    db,
                    self.bus,
                    kind="override_not_applied",
                    level="warning",
                    pair_id=pair.id,
                    account_id=account.id,
                    message=(
                        f"{pair.name}: override not applied: plan changed from "
                        f"{approved or 0} to {deletions} deletions"
                    ),
                )
        if over and not override_used:
            return self._block(db, pair, account, dry, planned.planned, res, deletions)
        self.finish_job(db, dry, JobState.SUCCESS, res)
        pair.override_next = False
        db.commit()
        # One-way sync has no bisync percentage brake, so cap the real run's deletes too.
        cap = None
        if not bidirectional:
            cap = deletions if override_used else max(deletions, pair.max_deletions)

        # 3. real run
        job = self.start_job(db, pair, JobKind.BISYNC if bidirectional else JobKind.SYNC, trigger)
        job.override_used = override_used
        job.planned_deletions = deletions
        db.commit()
        counters = Counters()

        def on_real(line: LogLine) -> None:
            counters.feed(line)
            if line.stats:
                p = progress_from_stats(line.stats)
                job.bytes_transferred = p.bytes
                self.bus.publish(
                    "progress",
                    {
                        "pair_id": pair.id,
                        "job_id": job.id,
                        "bytes": p.bytes,
                        "total_bytes": p.total_bytes,
                        "speed": p.speed,
                        "eta": p.eta,
                        "transferring": p.transferring,
                    },
                )

        res = await self.run(
            self._args(account, pair, dry_run=False, bidirectional=bidirectional, max_delete=cap),
            job,
            on_real,
        )
        job.files_uploaded = counters.uploaded
        job.files_downloaded = counters.downloaded
        job.files_deleted_local = counters.deleted_local
        job.files_deleted_remote = counters.deleted_remote
        job.files_changed = counters.uploaded + counters.downloaded + counters.deletions
        failure = classify_failure(res.exit_code, res.error_tail)
        local_dir = pair_local_dir(self.layout, account, pair)
        if failure is not None:
            # Conflict copies rclone already made must still be shown, even if the run failed.
            job.conflicts = record_conflicts(
                db,
                pair,
                local_dir,
                counters.conflict_paths,
                counters.conflict_variants,
                require_files=True,
            )
            self.finish_job(db, job, JobState.FAILED, res)
            await self.handle_failure(db, pair, account, failure, res)
            return job.id

        # 4. success
        job.conflicts = record_conflicts(
            db, pair, local_dir, counters.conflict_paths, counters.conflict_variants
        )
        open_conflicts = (
            db.scalar(
                select(func.count())
                .select_from(Conflict)
                .where(Conflict.pair_id == pair.id, Conflict.state == ConflictState.OPEN)
            )
            or 0
        )
        self.finish_job(db, job, JobState.SUCCESS, res)
        pair.last_success_at = self.clock()
        pair.fail_count = 0
        pair.next_retry_at = None
        pair.last_error = None
        if open_conflicts:
            self.set_status(db, pair, PairStatus.ATTENTION, AttentionReason.CONFLICTS)
        else:
            self.set_status(db, pair, PairStatus.HEALTHY)
        record_event(
            db,
            self.bus,
            kind="sync_done",
            pair_id=pair.id,
            account_id=account.id,
            message=(
                f"{pair.name} sync completed: {counters.uploaded} uploaded, "
                f"{counters.downloaded} downloaded, {counters.deletions} deleted"
                + (f", {job.conflicts} conflicts" if job.conflicts else "")
            ),
            data={"job_id": job.id},
        )
        return job.id
