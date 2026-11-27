"""A small in-process sliding-window rate limiter.

Used by the public pin routes, which trigger third-party API calls on a
visitor's click. It is per worker process and per container — good enough
to stop one browser hammering NLDI, not a distributed quota. Keys are
whatever the caller chooses (a page token, or a visitor key from
`client_key`, which folds an IPv6 address to its /64 so a visitor rotating
addresses inside one allocation still lands in one bucket).
"""

import ipaddress
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


class ConcurrencyLimiter:
    """Allow at most `max_in_flight` simultaneous operations for each key.

    Pair every successful `acquire` with a `release` (in a `finally`), or
    the slot leaks until the process restarts.
    """

    def __init__(self, max_in_flight):
        self.max_in_flight = int(max_in_flight)
        self._counts = {}
        self._lock = threading.Lock()

    def acquire(self, key):
        """Take a slot for `key`; return False (taking nothing) if it is full."""
        with self._lock:
            count = self._counts.get(key, 0)
            if count >= self.max_in_flight:
                return False
            self._counts[key] = count + 1
            return True

    def release(self, key):
        """Give back a slot taken by `acquire`; idle keys are forgotten."""
        with self._lock:
            count = self._counts.get(key, 0) - 1
            if count > 0:
                self._counts[key] = count
            else:
                self._counts.pop(key, None)

    def in_flight(self, key):
        with self._lock:
            return self._counts.get(key, 0)

    def tracked_keys(self):
        """Number of keys currently holding a slot (tests)."""
        with self._lock:
            return len(self._counts)

    def reset(self):
        """Forget every slot (tests)."""
        with self._lock:
            self._counts.clear()


def client_key(addr):
    """Rate-limit key for a client address.

    IPv4 addresses are used as-is. An IPv6 address is folded to its /64
    network, the smallest allocation an ISP hands one subscriber, so cycling
    addresses inside it does not reset a limit. IPv4-mapped IPv6 addresses
    (``::ffff:a.b.c.d``) key on the embedded IPv4 address, otherwise every
    such client would share the ``::/64`` bucket. Anything unparseable is
    returned verbatim.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except (TypeError, ValueError):
        return addr
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)
