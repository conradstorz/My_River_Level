from web.ratelimit import RateLimiter


def test_allows_up_to_limit_then_refuses():
    rl = RateLimiter(limit=3, per_seconds=60)
    assert [rl.allow("k", now=0), rl.allow("k", now=1), rl.allow("k", now=2)] == [True, True, True]
    assert rl.allow("k", now=3) is False


def test_window_slides():
    rl = RateLimiter(limit=2, per_seconds=10)
    assert rl.allow("k", now=0) and rl.allow("k", now=5)
    assert rl.allow("k", now=9) is False
    assert rl.allow("k", now=10.1) is True     # first hit at t=0 has expired


def test_keys_are_independent():
    rl = RateLimiter(limit=1, per_seconds=60)
    assert rl.allow("a", now=0) is True
    assert rl.allow("b", now=0) is True
    assert rl.allow("a", now=1) is False


def test_reset_clears_everything():
    rl = RateLimiter(limit=1, per_seconds=60)
    rl.allow("a", now=0)
    rl.reset()
    assert rl.allow("a", now=1) is True


def test_idle_keys_are_pruned_once_the_table_is_large():
    rl = RateLimiter(limit=1, per_seconds=10)
    rl.PRUNE_AT = 5
    for i in range(5):
        rl.allow(f"k{i}", now=0)
    assert rl.tracked_keys() == 5
    rl.allow("fresh", now=20)                 # every k* hit expired at t=10
    assert rl.tracked_keys() == 1


def test_prune_keeps_keys_with_live_hits():
    rl = RateLimiter(limit=1, per_seconds=10)
    rl.PRUNE_AT = 2
    rl.allow("old", now=0)
    rl.allow("live", now=15)
    rl.allow("new", now=16)                   # triggers a prune at cutoff=6
    assert rl.tracked_keys() == 2
    assert rl.allow("live", now=17) is False  # its hit at t=15 survived

