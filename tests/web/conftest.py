import pytest

from web import routes as routes_mod


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """The route limiters are module globals and every test client shares one
    address, so start each web test with empty buckets."""
    routes_mod.reset_limiters()
    yield
    routes_mod.reset_limiters()
