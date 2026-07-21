from monitor.search_cache import get_or_compute, clear


def setup_function():
    clear()


def test_caches_within_ttl_producer_called_once():
    calls = {"n": 0}
    def produce():
        calls["n"] += 1
        return "v"
    assert get_or_compute("k", produce, ttl=100, clock=lambda: 0) == "v"
    assert get_or_compute("k", produce, ttl=100, clock=lambda: 50) == "v"
    assert calls["n"] == 1


def test_recomputes_after_expiry():
    seq = {"n": 0}
    def produce():
        seq["n"] += 1
        return seq["n"]
    assert get_or_compute("k", produce, ttl=10, clock=lambda: 0) == 1
    assert get_or_compute("k", produce, ttl=10, clock=lambda: 20) == 2  # expired


def test_distinct_keys_isolated():
    assert get_or_compute("a", lambda: 1, clock=lambda: 0) == 1
    assert get_or_compute("b", lambda: 2, clock=lambda: 0) == 2


def test_store_is_bounded_under_many_distinct_keys():
    from monitor import search_cache
    # Far more distinct keys than the bound; store must not grow without limit.
    for i in range(search_cache._MAX_ENTRIES * 3):
        get_or_compute(f"k{i}", lambda i=i: i, ttl=100, clock=lambda: 0)
    assert len(search_cache._STORE) <= search_cache._MAX_ENTRIES
