from concurrent.futures import ThreadPoolExecutor

from app.auth.ratelimit import LoginLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_concurrent_attempts_allow_at_most_max_failures():
    limiter = LoginLimiter(max_failures=5)
    verified = []

    def attempt(_):
        if limiter.try_acquire("k"):
            verified.append(1)  # stands in for a (failing) password verification

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(attempt, range(20)))
    assert len(verified) == 5
    assert limiter.is_locked("k")


def test_lock_expiry_unlocks_without_errors():
    clock = FakeClock()
    limiter = LoginLimiter(max_failures=2, lockout_s=100, clock=clock)
    assert limiter.try_acquire("k") and limiter.try_acquire("k")
    assert not limiter.try_acquire("k")
    clock.now += 101
    assert not limiter.is_locked("k")
    assert not limiter.is_locked("k")
    assert limiter.try_acquire("k")


def test_window_expiry_forgets_old_failures():
    clock = FakeClock()
    limiter = LoginLimiter(max_failures=3, window_s=50, lockout_s=100, clock=clock)
    limiter.try_acquire("k")
    limiter.try_acquire("k")
    clock.now += 60
    assert limiter.try_acquire("k")
    assert limiter.try_acquire("k")
    assert not limiter.is_locked("k")


def test_success_clears_state():
    limiter = LoginLimiter(max_failures=2)
    limiter.try_acquire("k")
    limiter.success("k")
    limiter.try_acquire("k")
    assert not limiter.is_locked("k")


def test_old_keys_are_pruned():
    clock = FakeClock()
    limiter = LoginLimiter(max_failures=2, window_s=50, lockout_s=100, clock=clock)
    limiter.try_acquire("a")
    limiter.try_acquire("b")
    limiter.try_acquire("b")  # b locked
    clock.now += 200
    limiter.try_acquire("c")
    assert set(limiter._failures) == {"c"}
    assert limiter._locked_until == {}
