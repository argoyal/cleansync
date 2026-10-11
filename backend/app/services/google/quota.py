"""The cost guard's parts (spec §7): what each Google call costs, one daily budget for the
whole Google Cloud project, and a pace per mailbox."""

import asyncio
import logging
import threading
import time
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import utcnow
from app.models import ApiUsage

log = logging.getLogger(__name__)

# Quota units per call. Google's 2026 numbers are not final: where sources disagree the
# higher number is counted (messages.get: Google lists 5, 2026 guides say 20). Correct the
# numbers here, in this one place, when Google publishes them.
UNIT_COSTS: dict[str, int] = {
    "gmail.profile": 1,
    "gmail.labels.list": 1,
    "gmail.messages.list": 5,
    "gmail.messages.get": 20,
    "gmail.history.list": 2,
    "gmail.messages.trash": 5,
    "gmail.messages.untrash": 5,
    "gmail.messages.delete": 10,
    "gmail.messages.import": 25,
    # Drive API calls CleanSync makes itself (rclone's own calls are estimated below).
    "drive.files.get": 1,
    "drive.files.create": 1,
    "drive.files.delete": 1,
}
# rclone's Drive calls don't go through CleanSync's client: each run is estimated.
DRIVE_UNITS_PER_RUN = 100
DRIVE_UNITS_PER_FILE = 10
# Half of Google's 6,000 units a minute per user.
PER_MAILBOX_UNITS_PER_MINUTE = 3000
COUNTED = "counted"
DRIVE_ESTIMATE = "drive_estimate"
SAVE_EVERY_UNITS = 5000
BUDGET_READ_EVERY_S = 60.0
BUDGET_MSG = (
    "Paused: CleanSync used its Google API budget for today ({budget:,} units). "
    "It goes on after midnight UTC."
)


def cost(op: str) -> int:
    return UNIT_COSTS[op]


def drive_run_estimate(files_changed: int) -> int:
    return DRIVE_UNITS_PER_RUN + DRIVE_UNITS_PER_FILE * max(0, files_changed)


class BudgetReached(Exception):
    def __init__(self, budget: int) -> None:
        super().__init__(BUDGET_MSG.format(budget=budget))
        self.budget = budget


class ApiBudget:
    """Units used per UTC day, across all accounts. Kept in memory, saved to the database
    every few thousand units, when the day changes and on flush()."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        budget: Callable[[], int],
        clock=utcnow,
        wall: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sf = session_factory
        self._read_budget = budget
        self.clock = clock
        self.wall = wall
        self._lock = threading.Lock()
        self._day: str | None = None
        self._used = {COUNTED: 0, DRIVE_ESTIMATE: 0}
        self._unsaved = 0
        self._budget_cache: tuple[int, float] | None = None

    def day(self) -> str:
        return self.clock().date().isoformat()

    def budget(self) -> int:
        cached = self._budget_cache
        if cached is None or self.wall() - cached[1] >= BUDGET_READ_EVERY_S:
            cached = (int(self._read_budget()), self.wall())
            self._budget_cache = cached
        return cached[0]

    def _roll(self) -> None:
        """With the lock held: start a new day when the UTC date changed."""
        today = self.day()
        if today == self._day:
            return
        if self._day is not None:
            self._save()
        loaded = {COUNTED: 0, DRIVE_ESTIMATE: 0}
        with self.sf() as db:
            for bucket in loaded:
                row = db.get(ApiUsage, (today, bucket))
                loaded[bucket] = row.units if row is not None else 0
        # Only after the read worked: a failed read leaves the old day in place, to retry.
        self._used = loaded
        self._day = today
        self._unsaved = 0

    def _save(self) -> None:
        with self.sf() as db:
            for bucket, units in self._used.items():
                row = db.get(ApiUsage, (self._day, bucket))
                if row is None:
                    db.add(ApiUsage(day=self._day, bucket=bucket, units=units))
                else:
                    row.units = units
            db.commit()
        self._unsaved = 0

    def used_today(self) -> int:
        with self._lock:
            self._roll()
            return self._used[COUNTED]

    def drive_estimate_today(self) -> int:
        with self._lock:
            self._roll()
            return self._used[DRIVE_ESTIMATE]

    def reached(self) -> bool:
        return self.used_today() >= self.budget()

    def spend(self, units: int, *, enforce: bool = True) -> None:
        """Count a call before it is made. With enforce, refuse it when it would go over."""
        limit = self.budget()
        with self._lock:
            self._roll()
            if enforce and self._used[COUNTED] + units > limit:
                raise BudgetReached(limit)
            self._used[COUNTED] += units
            self._unsaved += units
            if self._unsaved >= SAVE_EVERY_UNITS:
                self._save()

    def add_drive_estimate(self, units: int) -> None:
        with self._lock:
            self._roll()
            self._used[DRIVE_ESTIMATE] += units
            self._save()

    def flush(self) -> None:
        with self._lock:
            if self._day is not None:
                self._save()


class Pacer:
    """At most `per_minute` units a minute for each mailbox: a bucket that holds one
    minute's worth, so short bursts need no wait."""

    def __init__(
        self,
        per_minute: int = PER_MAILBOX_UNITS_PER_MINUTE,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep=asyncio.sleep,
    ) -> None:
        self.rate = per_minute / 60.0
        self.capacity = float(per_minute)
        self.clock = clock
        self.sleep = sleep
        self._buckets: dict[str, tuple[float, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, key: str, units: int) -> None:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = self.clock()
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < units:
                await self.sleep((units - tokens) / self.rate)
                later = self.clock()
                tokens = min(self.capacity, tokens + (later - now) * self.rate)
                now = later
            self._buckets[key] = (tokens - units, now)
