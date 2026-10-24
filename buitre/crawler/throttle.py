import threading
import time
from typing import Callable


class HostThrottle:
    """
    Per-host politeness: at least `delay` seconds between the *starts* of
    two requests to the same host, even with many worker threads.

    Each caller reserves the next free slot under a lock and then sleeps
    outside it, so threads on different hosts never wait for each other.
    """

    def __init__(
        self,
        delay: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delay = delay
        self._clock = clock
        self._sleep = sleep
        self._last_start: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str, min_delay: float | None = None) -> float:
        """Block until `host` may be fetched. Returns seconds waited."""

        delay = max(self.delay, min_delay or 0.0)

        if delay <= 0:
            return 0.0

        with self._lock:
            now = self._clock()

            # Spacing is measured from the previous request's start with
            # the *current* delay, so a Crawl-delay learned mid-crawl
            # applies immediately.
            previous = self._last_start.get(host)
            slot = now if previous is None else max(now, previous + delay)
            self._last_start[host] = slot

        wait = slot - now

        if wait > 0:
            self._sleep(wait)

        return max(wait, 0.0)
