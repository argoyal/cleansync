import threading
import time
from collections.abc import Callable


class LoginLimiter:
    """Thread-safe attempt limiter.

    try_acquire() checks the lock and counts the attempt in one step, so parallel
    guesses cannot slip past the limit. success() forgets the key; a failed attempt
    simply stays counted.
    """

    def __init__(
        self,
        max_failures: int = 5,
        window_s: float = 900,
        lockout_s: float = 900,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.lockout_s = lockout_s
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        for key in [k for k, until in self._locked_until.items() if now >= until]:
            self._locked_until.pop(key, None)
        for key in list(self._failures):
            recent = [t for t in self._failures[key] if now - t < self.window_s]
            if recent:
                self._failures[key] = recent
            else:
                self._failures.pop(key, None)

    def is_locked(self, key: str) -> bool:
        with self._lock:
            self._prune(self.clock())
            return key in self._locked_until

    def try_acquire(self, key: str) -> bool:
        with self._lock:
            now = self.clock()
            self._prune(now)
            if key in self._locked_until:
                return False
            recent = self._failures.get(key, [])
            recent.append(now)
            if len(recent) >= self.max_failures:
                self._locked_until[key] = now + self.lockout_s
                recent = []
            if recent:
                self._failures[key] = recent
            else:
                self._failures.pop(key, None)
            return True

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    reset = success
