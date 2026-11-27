from web.ratelimit import ConcurrencyLimiter, RateLimiter, client_key


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


# ── Visitor keys ────────────────────────────────────────────────────────────

def test_ipv4_key_is_the_address():
    assert client_key("203.0.113.5") == "203.0.113.5"
    assert client_key("203.0.113.5") != client_key("203.0.113.6")


def test_ipv6_addresses_in_one_slash64_share_a_key():
    assert client_key("2001:db8:1:2::1") == client_key("2001:db8:1:2:ffff::9")
    assert client_key("2001:db8:1:2::1") != client_key("2001:db8:1:3::1")


def test_ipv4_mapped_ipv6_keys_on_the_ipv4_address():
    assert client_key("::ffff:203.0.113.5") == "203.0.113.5"
    assert client_key("::ffff:203.0.113.5") != client_key("::ffff:203.0.113.6")


def test_unparseable_address_is_used_verbatim():
    assert client_key("unknown") == "unknown"


# ── Concurrency limiter ─────────────────────────────────────────────────────

def test_concurrency_limiter_caps_in_flight_per_key():
    cl = ConcurrencyLimiter(max_in_flight=2)
    assert cl.acquire("a") and cl.acquire("a")
    assert cl.acquire("a") is False
    assert cl.acquire("b") is True
    cl.release("a")
    assert cl.acquire("a") is True


def test_concurrency_limiter_forgets_idle_keys():
    cl = ConcurrencyLimiter(max_in_flight=1)
    cl.acquire("a")
    cl.release("a")
    assert cl.in_flight("a") == 0
    assert cl.tracked_keys() == 0
