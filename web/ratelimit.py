"""A small in-process sliding-window rate limiter.

Used by the public pin routes, which trigger third-party API calls on a
visitor's click. It is per worker process and per container — good enough
to stop one browser hammering NLDI, not a distributed quota. Keys are
whatever the caller chooses (a page token, a client IP).
"""

import threading
import time
from collections import deque


class RateLimiter:
    """Allow at most `limit` calls per `per_seconds` for each key."""

    #: Once this many keys are tracked, the next call sweeps out keys whose
    #: hits have all expired, so idle tokens and IPs do not accumulate forever.
    PRUNE_AT = 1024

    def __init__(self, limit, per_seconds):
        self.limit = int(limit)
        self.per_seconds = float(per_seconds)
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key, now=None):
        """Record a hit for `key` and return True if it is within the limit."""
        now = time.monotonic() if now is None else now
        with self._lock:
            cutoff = now - self.per_seconds
            if len(self._hits) >= self.PRUNE_AT:
                self._prune(cutoff)
            window = self._hits.setdefault(key, deque())
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= self.limit:
                return False
            window.append(now)
            return True

    def _prune(self, cutoff):
        """Drop every key whose newest hit is at or before `cutoff`. Caller holds the lock."""
        stale = [k for k, w in self._hits.items() if not w or w[-1] <= cutoff]
        for k in stale:
            del self._hits[k]

    def tracked_keys(self):
        """Number of keys currently held (tests)."""
        with self._lock:
            return len(self._hits)

    def reset(self):
        """Forget every recorded hit (tests)."""
        with self._lock:
            self._hits.clear()
