"""In-process sliding-window rate limiter.

This is deliberately simple: limits are kept in memory, so they apply per
API process. That is adequate for the single-container deployment this
project ships with; a multi-instance deployment would need a shared store
(documented in docs/SECURITY.md).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: float) -> tuple[bool, float]:
        """Record an attempt. Returns (allowed, seconds_until_retry)."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= window_s:
                hits.popleft()
            if len(hits) >= limit:
                return False, window_s - (now - hits[0])
            hits.append(now)
            return True, 0.0

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


limiter = RateLimiter()
