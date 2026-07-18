import os
from urllib.parse import urlparse, urlunparse

import psycopg2
from psycopg2 import sql
import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://river:river@localhost:5432/river_test"
)


def _ensure_test_database():
    """Create the test database if it doesn't already exist.

    Lets the suite bootstrap itself against a fresh PostgreSQL server
    (e.g. a container) without a manual ``createdb`` step, so the tests
    run unchanged whether the DB is local or on a remote Docker host.
    """
    parsed = urlparse(TEST_DATABASE_URL)
    dbname = parsed.path.lstrip("/")
    admin_url = urlunparse(parsed._replace(path="/postgres"))
    conn = psycopg2.connect(admin_url)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if not cur.fetchone():
                cur.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname))
                )
    finally:
        conn.close()

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

@pytest.fixture(scope="session", autouse=True)
def _test_database():
    """Ensure the test database exists before any test connects."""
    _ensure_test_database()


@pytest.fixture
def tmp_db(_test_database):
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
