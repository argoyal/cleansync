import time
from collections.abc import Callable


class LoginLimiter:
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
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def is_locked(self, key: str) -> bool:
        until = self._locked_until.get(key)
        if until is None:
            return False
        if self.clock() >= until:
            del self._locked_until[key]
            return False
        return True

    def record_failure(self, key: str) -> None:
        now = self.clock()
        recent = [t for t in self._failures.get(key, []) if now - t < self.window_s]
        recent.append(now)
        if len(recent) >= self.max_failures:
            self._locked_until[key] = now + self.lockout_s
            recent = []
        self._failures[key] = recent

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)
