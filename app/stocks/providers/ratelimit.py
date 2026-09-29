import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """Blocks so that at most `calls` acquisitions happen in any `period` seconds.

    Thread-safe: a backfill thread and a web request share one limiter per provider, and the
    waiting caller holds the lock, so the others queue behind it."""

    def __init__(
        self,
        calls: int,
        period: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if calls < 1 or period <= 0:
            raise ValueError("calls must be >= 1 and period > 0")
        self.calls = calls
        self.period = period
        self._clock = clock
        self._sleep = sleep
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            while self._times and self._times[0] <= now - self.period:
                self._times.popleft()
            if len(self._times) >= self.calls:
                self._sleep(self._times[0] + self.period - now)
                self._times.popleft()
                now = self._clock()
            self._times.append(now)
