"""Tiny in-process TTL cache so paginated searches reuse a ranked pool instead
of re-hitting the upstream APIs on every GET page request. Not shared across
processes — good enough for the single-container web server."""

import time

_STORE = {}  # key -> (expires_at, value)
_MAX_ENTRIES = 256  # keys come from a public search box — bound the store


def get_or_compute(key, producer, ttl=120, clock=time.monotonic):
    """Return the cached value for `key` if unexpired, else compute and store it.

    The store is bounded (keys are user-supplied search text): before inserting
    a new key into a full store, expired entries are purged and, if still full,
    the soonest-to-expire entries are evicted.
    """
    now = clock()
    hit = _STORE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = producer()
    if key not in _STORE and len(_STORE) >= _MAX_ENTRIES:
        for k in [k for k, (exp, _v) in _STORE.items() if exp <= now]:
            del _STORE[k]
        while len(_STORE) >= _MAX_ENTRIES:
            del _STORE[min(_STORE, key=lambda k: _STORE[k][0])]
    _STORE[key] = (now + ttl, value)
    return value


def clear():
    """Empty the cache (used by tests)."""
    _STORE.clear()
