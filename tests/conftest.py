import pytest
import psycopg2
import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://river:river@localhost:5432/river_test"
)

_DROP_ALL = """
DROP TABLE IF EXISTS
    page_subscribers,
    page_noaa_gauges,
    noaa_gauges,
    user_pages,
    pending_registrations,
    notifications,
    subscribers,
    site_conditions,
    settings,
    sites
CASCADE;
"""

@pytest.fixture
def tmp_db():
    """Provides a PostgreSQL test database URL with clean tables."""
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(_DROP_ALL)
    cur.close()
    conn.close()

    from db.models import init_db
    init_db(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
