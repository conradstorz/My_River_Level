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
