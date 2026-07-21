"""Tiny in-process TTL cache so paginated searches reuse a ranked pool instead
of re-hitting the upstream APIs on every GET page request. Not shared across
processes — good enough for the single-container web server."""

import time

_STORE = {}  # key -> (expires_at, value)


def get_or_compute(key, producer, ttl=120, clock=time.monotonic):
    """Return the cached value for `key` if unexpired, else compute and store it."""
    now = clock()
    hit = _STORE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = producer()
    _STORE[key] = (now + ttl, value)
    return value


def clear():
    """Empty the cache (used by tests)."""
    _STORE.clear()
