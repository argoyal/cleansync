import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.models import ApiUsage
from app.services.google.quota import (
    BUDGET_MSG,
    UNIT_COSTS,
    ApiBudget,
    BudgetReached,
    Pacer,
    cost,
    drive_run_estimate,
)


def test_one_table_has_a_cost_for_every_call_cleansync_makes():
    assert set(UNIT_COSTS) == {
        "gmail.profile",
        "gmail.labels.list",
        "gmail.messages.list",
        "gmail.messages.get",
        "gmail.history.list",
        "gmail.messages.trash",
        "gmail.messages.untrash",
        "gmail.messages.modify",
        "gmail.labels.create",
        "gmail.messages.import",
        "gmail.messages.import.upload",
        "drive.files.get",
        "drive.files.create",
        "drive.files.delete",
    }
    assert cost("gmail.messages.get") == 20  # the higher of the published numbers
    assert cost("gmail.messages.import.upload") == 0  # a resumable import counts once
    assert drive_run_estimate(0) == 100
    assert drive_run_estimate(3) == 130
    with pytest.raises(KeyError):
        cost("gmail.something.new")  # a new call must get a cost first


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 10, 23, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


def test_the_budget_counts_per_utc_day_and_stops_at_the_line(session_factory, db):
    clock = Clock()
    b = ApiBudget(session_factory, budget=lambda: 100, clock=clock)
    b.spend(60)
    b.spend(40)
    with pytest.raises(BudgetReached) as e:
        b.spend(1)
    assert str(e.value) == BUDGET_MSG.format(budget=100)
    assert "It goes on after midnight UTC" in str(e.value)
    b.spend(5, enforce=False)  # Drive calls are counted but never refused
    assert b.used_today() == 105 and b.reached()
    clock.now += timedelta(hours=2)  # a new UTC day
    assert b.used_today() == 0 and not b.reached()
    b.spend(1)
    row = db.get(ApiUsage, ("2026-10-10", "counted"))
    assert row is not None and row.units == 105  # the old day was saved when the day changed


def test_the_budget_survives_a_restart(session_factory):
    clock = Clock()
    first = ApiBudget(session_factory, budget=lambda: 10_000, clock=clock)
    first.spend(250)
    first.add_drive_estimate(130)
    first.flush()
    again = ApiBudget(session_factory, budget=lambda: 10_000, clock=clock)
    assert again.used_today() == 250
    assert again.drive_estimate_today() == 130


def test_the_drive_estimate_is_shown_and_never_counts_against_the_budget(session_factory):
    b = ApiBudget(session_factory, budget=lambda: 100, clock=Clock())
    b.add_drive_estimate(1_000_000)
    assert b.used_today() == 0 and not b.reached()
    b.spend(100)


async def test_the_pacer_lets_a_burst_through_then_waits():
    now = [0.0]
    waits: list[float] = []

    async def sleep(s):
        waits.append(s)
        now[0] += s

    p = Pacer(60, clock=lambda: now[0], sleep=sleep)  # 1 unit a second, 60 at once
    await p.wait("gd_alice", 60)
    assert waits == []
    await p.wait("gd_alice", 20)
    assert waits == [pytest.approx(20)]
    await p.wait("gd_bob", 60)  # each mailbox has its own pace
    assert len(waits) == 1


def test_a_failed_read_on_a_new_day_does_not_carry_the_old_days_units_over(session_factory, db):
    clock = Clock()
    calls_until_failure = [None]  # None: never fail

    def flaky():
        if calls_until_failure[0] is not None:
            calls_until_failure[0] -= 1
            if calls_until_failure[0] < 0:
                calls_until_failure[0] = None
                raise RuntimeError("database is locked")
        return session_factory()

    b = ApiBudget(flaky, budget=lambda: 1000, clock=clock)
    b.spend(50)
    clock.now += timedelta(hours=2)  # a new UTC day
    calls_until_failure[0] = 1  # the old day is saved, then the new day's read fails once
    with pytest.raises(RuntimeError):
        b.used_today()
    assert b.used_today() == 0  # tried again, and the old day's 50 are not counted today
    b.spend(1)
    b.flush()
    assert db.get(ApiUsage, ("2026-10-10", "counted")).units == 50
    assert db.get(ApiUsage, ("2026-10-11", "counted")).units == 1


def test_many_threads_spending_at_once_lose_nothing(session_factory):
    b = ApiBudget(session_factory, budget=lambda: 1_000_000, clock=Clock())

    def work():
        for _ in range(1000):
            b.spend(1)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert b.used_today() == 8000
