# Docker Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate the River Monitor from a Windows Service + SQLite to a Dockerized Python app + PostgreSQL, deployable to any cloud server.

**Architecture:** Two containers via docker-compose (`app` + `db`). The `app` container runs all threads and Flask on port 5743. The `db` container is PostgreSQL with a named volume. Production uses a managed PostgreSQL instance via `DATABASE_URL` env var.

**Tech Stack:** Python 3.11, psycopg2-binary, Flask, Docker, docker-compose, PostgreSQL 16, GitHub Container Registry (ghcr.io)

---

### Task 1: Update requirements.txt

**Files:**
- Modify: `requirements.txt`

**Step 1: Remove Windows-only and terminal-UI packages, add psycopg2**

Replace the contents of `requirements.txt` with:

```
# USGS Water Data Tools
dataretrieval

# Data Analysis
pandas
numpy

# API and Web Requests
requests

# Visualization (optional)
matplotlib

# Database
psycopg2-binary

# Web portal
flask

# Notification channels
python-telegram-bot>=20.0
twilio

# Testing
pytest
pytest-mock
```

**Step 2: Commit**

```bash
git add requirements.txt
git commit -m "chore: remove pywin32/rich/colorama, add psycopg2-binary"
```

---

### Task 2: Update conftest.py for PostgreSQL

**Files:**
- Modify: `tests/conftest.py`

The `tmp_db` fixture currently returns a SQLite file path. It now returns a PostgreSQL URL string pointing to a clean test database. All existing tests pass this string as `db_path` to model functions — unchanged interface.

**Prerequisite:** PostgreSQL must be running locally. The quickest way during development is:
```bash
docker run -d --name river-test-db -e POSTGRES_USER=river -e POSTGRES_PASSWORD=river -e POSTGRES_DB=river_test -p 5432:5432 postgres:16
```

**Step 1: Rewrite conftest.py**

```python
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
```

**Step 2: Verify fixture loads (no test run yet — models.py isn't migrated)**

This step is preparatory. The fixture will be verified in Task 4.

**Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: update tmp_db fixture for PostgreSQL"
```

---

### Task 3: Rewrite db/models.py for PostgreSQL

**Files:**
- Modify: `db/models.py`

This is the largest change. Replace the entire file. Key differences from SQLite:
- `psycopg2` replaces `sqlite3`
- `%s` placeholders replace `?`
- `SERIAL PRIMARY KEY` replaces `INTEGER PRIMARY KEY AUTOINCREMENT`
- `INSERT ... ON CONFLICT DO NOTHING` replaces `INSERT OR IGNORE`
- `INSERT ... ON CONFLICT DO UPDATE SET` replaces `INSERT OR REPLACE`
- `RealDictCursor` replaces `sqlite3.Row` (same `row["column"]` access pattern)
- `get_conn(db_url=None)` replaces `get_db(db_path=None)` — but `get_db` is kept as an alias
- The `db_path` parameter in all functions is reused as a PostgreSQL URL string (or None → env var)

**Step 1: Write the new db/models.py**

```python
import os
import uuid
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://river:river@db:5432/rivermonitor"
)

DEFAULT_SETTINGS = {
    "poll_interval_minutes": "15",
    "low_percentile": "10",
    "high_percentile": "90",
    "very_low_percentile": "5",
    "very_high_percentile": "95",
    "reminder_low_high_hours": "24",
    "reminder_severe_hours": "4",
    "historical_start_year": "1980",
    "search_radius_miles": "25",
    "telegram_bot_token": "",
    "twilio_account_sid": "",
    "twilio_auth_token": "",
    "twilio_sms_number": "",
    "twilio_whatsapp_number": "",
    "facebook_page_token": "",
    "facebook_verify_token": "",
}

SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS sites (
        id SERIAL PRIMARY KEY,
        site_number TEXT NOT NULL UNIQUE,
        station_name TEXT NOT NULL DEFAULT '',
        parameter_code TEXT NOT NULL DEFAULT '00060',
        active INTEGER NOT NULL DEFAULT 1,
        added_at TEXT NOT NULL DEFAULT (NOW()::TEXT)
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS site_conditions (
        id SERIAL PRIMARY KEY,
        site_id INTEGER NOT NULL REFERENCES sites(id),
        checked_at TEXT NOT NULL DEFAULT (NOW()::TEXT),
        current_value DOUBLE PRECISION,
        unit TEXT NOT NULL DEFAULT 'cfs',
        percentile DOUBLE PRECISION,
        severity TEXT NOT NULL DEFAULT 'UNKNOWN'
    )""",
    """CREATE TABLE IF NOT EXISTS subscribers (
        id SERIAL PRIMARY KEY,
        display_name TEXT NOT NULL DEFAULT '',
        channel TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        opted_in_at TEXT NOT NULL DEFAULT (NOW()::TEXT),
        active INTEGER NOT NULL DEFAULT 1,
        UNIQUE(channel, channel_id)
    )""",
    """CREATE TABLE IF NOT EXISTS notifications (
        id SERIAL PRIMARY KEY,
        subscriber_id INTEGER REFERENCES subscribers(id),
        site_id INTEGER REFERENCES sites(id),
        sent_at TEXT NOT NULL DEFAULT (NOW()::TEXT),
        channel TEXT NOT NULL,
        message_text TEXT NOT NULL,
        trigger_type TEXT NOT NULL,
        success INTEGER NOT NULL DEFAULT 1,
        error_msg TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS pending_registrations (
        id SERIAL PRIMARY KEY,
        channel TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        started_at TEXT NOT NULL DEFAULT (NOW()::TEXT),
        UNIQUE(channel, channel_id)
    )""",
    """CREATE TABLE IF NOT EXISTS user_pages (
        id SERIAL PRIMARY KEY,
        public_token TEXT NOT NULL UNIQUE,
        edit_token TEXT NOT NULL UNIQUE,
        page_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (NOW()::TEXT),
        active INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS noaa_gauges (
        id SERIAL PRIMARY KEY,
        lid TEXT NOT NULL UNIQUE,
        station_name TEXT NOT NULL DEFAULT '',
        current_stage DOUBLE PRECISION,
        action_stage DOUBLE PRECISION,
        minor_flood_stage DOUBLE PRECISION,
        moderate_flood_stage DOUBLE PRECISION,
        major_flood_stage DOUBLE PRECISION,
        severity TEXT NOT NULL DEFAULT 'Normal'
            CHECK(severity IN ('Unknown', 'Normal', 'Action', 'Minor', 'Moderate', 'Major')),
        last_polled_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS page_noaa_gauges (
        page_id INTEGER NOT NULL REFERENCES user_pages(id),
        noaa_gauge_id INTEGER NOT NULL REFERENCES noaa_gauges(id),
        PRIMARY KEY (page_id, noaa_gauge_id)
    )""",
    """CREATE TABLE IF NOT EXISTS page_subscribers (
        id SERIAL PRIMARY KEY,
        page_id INTEGER NOT NULL REFERENCES user_pages(id),
        channel TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active', 'paused', 'unsubscribed')),
        opted_in_at TEXT NOT NULL DEFAULT (NOW()::TEXT),
        UNIQUE(page_id, channel, channel_id)
    )""",
]


def get_conn(db_url=None):
    """Return a new psycopg2 connection with RealDictCursor."""
    url = db_url or DATABASE_URL
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


# Alias: existing callers pass db_path — treat it as a URL
def get_db(db_path=None):
    return get_conn(db_path)


def init_db(db_path=None):
    """Create all tables and seed default settings."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    for stmt in SCHEMA_STATEMENTS:
        cur.execute(stmt)
    for key, value in DEFAULT_SETTINGS.items():
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
            (key, value)
        )
    conn.commit()
    cur.close()
    conn.close()


def get_setting(key, db_path=None, default=None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return default
    return row["value"]


def set_setting(key, value, db_path=None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, str(value))
    )
    conn.commit()
    cur.close()
    conn.close()


def create_user_page(page_name, db_path=None):
    """Create a new user page. Returns (public_token, edit_token)."""
    public_token = str(uuid.uuid4())
    edit_token = str(uuid.uuid4())
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO user_pages (public_token, edit_token, page_name) VALUES (%s, %s, %s)",
            (public_token, edit_token, page_name)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return public_token, edit_token


def get_page_by_public_token(token, db_path=None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM user_pages WHERE public_token=%s", (token,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return dict(row) if row else None


def get_page_by_edit_token(token, db_path=None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM user_pages WHERE edit_token=%s", (token,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return dict(row) if row else None


def get_or_create_noaa_gauge(lid, station_name, action_stage, minor_stage,
                              moderate_stage, major_stage, db_path=None):
    """Insert gauge if not present; return its id."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO noaa_gauges
               (lid, station_name, action_stage, minor_flood_stage, moderate_flood_stage, major_flood_stage)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (lid) DO NOTHING""",
            (lid, station_name, action_stage, minor_stage, moderate_stage, major_stage)
        )
        conn.commit()
        cur.execute("SELECT id FROM noaa_gauges WHERE lid=%s", (lid,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return row["id"]


def update_noaa_gauge_condition(lid, current_stage, severity, db_path=None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE noaa_gauges SET current_stage=%s, severity=%s, last_polled_at=(NOW()::TEXT)
               WHERE lid=%s""",
            (current_stage, severity, lid)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_all_noaa_gauges(db_path=None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM noaa_gauges")
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def link_page_gauge(page_id, gauge_id, db_path=None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO page_noaa_gauges (page_id, noaa_gauge_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (page_id, gauge_id)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def unlink_page_gauge(page_id, gauge_id, db_path=None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM page_noaa_gauges WHERE page_id=%s AND noaa_gauge_id=%s",
            (page_id, gauge_id)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_page_gauges(page_id, db_path=None):
    """Return all noaa_gauges linked to a page."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT ng.* FROM noaa_gauges ng
               JOIN page_noaa_gauges png ON png.noaa_gauge_id = ng.id
               WHERE png.page_id=%s""",
            (page_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def get_pages_for_noaa_gauge(gauge_id, db_path=None):
    """Return all active pages that include this gauge."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT up.* FROM user_pages up
               JOIN page_noaa_gauges png ON png.page_id = up.id
               WHERE png.noaa_gauge_id=%s AND up.active=1""",
            (gauge_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def add_page_subscriber(page_id, channel, channel_id, display_name, db_path=None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO page_subscribers
               (page_id, channel, channel_id, display_name, status)
               VALUES (%s, %s, %s, %s, 'active')
               ON CONFLICT (page_id, channel, channel_id)
               DO UPDATE SET display_name = EXCLUDED.display_name, status = 'active'""",
            (page_id, channel, channel_id, display_name)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def set_page_subscriber_status(page_id, channel, channel_id, status, db_path=None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE page_subscribers SET status=%s
               WHERE page_id=%s AND channel=%s AND channel_id=%s""",
            (status, page_id, channel, channel_id)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_active_page_subscribers(page_id, db_path=None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM page_subscribers WHERE page_id=%s AND status='active'",
            (page_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def get_page_subscribers_for_gauge(gauge_id, db_path=None):
    """Return all active page_subscribers for every page linked to this gauge."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT ps.* FROM page_subscribers ps
               JOIN page_noaa_gauges png ON png.page_id = ps.page_id
               WHERE png.noaa_gauge_id=%s AND ps.status='active'""",
            (gauge_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]
```

**Step 2: Run just the db model tests**

```bash
pytest tests/db/test_models.py -v
```

Expected: FAIL — `test_init_db_creates_all_tables` and `test_new_tables_created` use SQLite-specific `sqlite_master` queries.

**Step 3: Fix test_models.py SQLite-specific queries**

In `tests/db/test_models.py`, replace the two tests that query `sqlite_master`:

```python
# Replace test_init_db_creates_all_tables
def test_init_db_creates_all_tables(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
    """)
    tables = {row["table_name"] for row in cur.fetchall()}
    cur.close()
    conn.close()
    expected = {"sites", "settings", "site_conditions", "subscribers",
                "notifications", "pending_registrations",
                "user_pages", "noaa_gauges", "page_noaa_gauges", "page_subscribers"}
    assert expected.issubset(tables)

# Replace test_new_tables_created
def test_new_tables_created(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
    """)
    tables = {row["table_name"] for row in cur.fetchall()}
    cur.close()
    conn.close()
    assert "user_pages" in tables
    assert "noaa_gauges" in tables
    assert "page_noaa_gauges" in tables
    assert "page_subscribers" in tables
```

Also remove `import sqlite3` from line 3 of `test_models.py`.

**Step 4: Run db model tests again**

```bash
pytest tests/db/test_models.py -v
```

Expected: All PASS

**Step 5: Commit**

```bash
git add db/models.py tests/db/test_models.py
git commit -m "feat: migrate db/models.py from SQLite to PostgreSQL"
```

---

### Task 4: Delete obsolete test files

**Files:**
- Delete: `tests/db/test_migration.py` (tests db/migration.py which is being removed)
- Delete: `tests/test_launcher.py` (tests launch.py which is being removed)

**Step 1: Delete the files**

```bash
git rm tests/db/test_migration.py tests/test_launcher.py
```

**Step 2: Run the full test suite**

```bash
pytest -v
```

Expected: All remaining tests PASS (monitor and web tests may still work since polling/routes also use `db_path`)

**Step 3: Commit**

```bash
git commit -m "chore: remove test files for deleted modules"
```

---

### Task 5: Create main.py

**Files:**
- Create: `main.py`

`main.py` replaces `service.py` as the Docker entrypoint. It extracts the core logic from `run_service()` and binds Flask to `0.0.0.0` so it's reachable from outside the container. SIGTERM handling is provided by Docker automatically (Python exits on SIGTERM by default; the daemon threads stop when the main thread exits).

**Step 1: Create main.py**

```python
"""
River Monitor — Docker entrypoint

Usage: python main.py
"""

import os
import sys
import queue
import threading
import logging
import logging.handlers

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

LOG_PATH = os.path.join(BASE_DIR, "logs", "river_monitor.log")


def setup_logging():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3
        )
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s"
        ))
        root.addHandler(file_handler)
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(console)


def build_adapters():
    adapters = []
    try:
        from monitor.adapters.telegram import TelegramAdapter
        adapters.append(TelegramAdapter())
    except Exception as e:
        logging.warning("Telegram adapter unavailable: %s", e)
    try:
        from monitor.adapters.sms import SMSAdapter
        adapters.append(SMSAdapter())
    except Exception as e:
        logging.warning("SMS adapter unavailable: %s", e)
    try:
        from monitor.adapters.whatsapp import WhatsAppAdapter
        adapters.append(WhatsAppAdapter())
    except Exception as e:
        logging.warning("WhatsApp adapter unavailable: %s", e)
    try:
        from monitor.adapters.facebook import FacebookAdapter
        adapters.append(FacebookAdapter())
    except Exception as e:
        logging.warning("Facebook adapter unavailable: %s", e)
    return adapters


def main():
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("River Monitor starting")

    from db.models import init_db
    init_db()

    stop_event = threading.Event()
    notif_queue = queue.Queue()

    all_adapters = build_adapters()
    thread_adapters = [a for a in all_adapters if isinstance(a, threading.Thread)]

    from monitor.polling import PollingThread
    from monitor.noaa_polling import NoaaPollingThread
    from monitor.scheduler import SchedulerThread
    from monitor.dispatcher import NotificationDispatcher
    from web.app import create_app

    polling = PollingThread(notif_queue, stop_event=stop_event)
    noaa_polling = NoaaPollingThread(notif_queue, stop_event=stop_event)
    scheduler = SchedulerThread(notif_queue, stop_event=stop_event)
    dispatcher = NotificationDispatcher(
        notif_queue, adapters=all_adapters, stop_event=stop_event
    )

    flask_app = create_app(notification_queue=notif_queue)

    def run_flask():
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
        flask_app.run(host="0.0.0.0", port=5743, use_reloader=False, threaded=True)

    web_thread = threading.Thread(target=run_flask, name="WebThread", daemon=True)

    for t in thread_adapters:
        t.start()
    for t in [polling, noaa_polling, scheduler, dispatcher, web_thread]:
        t.start()

    logger.info("All threads started. Portal at http://localhost:5743")
    stop_event.wait()
    logger.info("Stop event received — shutting down")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nShutting down...")
```

**Note:** The adapters in `monitor/adapters/` currently accept `db_path` as a constructor argument. After this task, they will need to default to `db_path=None` (which already means "use DATABASE_URL"). Verify each adapter's `__init__` signature and ensure `db_path=None` is the default — if any adapter requires `db_path`, update to make it optional.

**Step 2: Check adapter constructors — look for required db_path args**

```bash
grep -n "def __init__" monitor/adapters/telegram.py monitor/adapters/sms.py monitor/adapters/whatsapp.py monitor/adapters/facebook.py
```

If any have `def __init__(self, db_path)` (no default), change to `def __init__(self, db_path=None)`.

**Step 3: Run tests**

```bash
pytest -v
```

Expected: All PASS

**Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add main.py as Docker entrypoint replacing service.py"
```

---

### Task 6: Update web/app.py

**Files:**
- Modify: `web/app.py`

The Flask secret key is currently hardcoded. It must come from an env var in production.

**Step 1: Update create_app to read SECRET_KEY from environment**

```python
import os
from flask import Flask
from db.models import DATABASE_URL


def create_app(db_path=None, notification_queue=None):
    app = Flask(__name__, template_folder="templates")
    app.config["DB_PATH"] = db_path  # may be None; routes use DATABASE_URL
    app.config["NOTIFICATION_QUEUE"] = notification_queue
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "river-monitor-dev-secret")

    from web.routes import register_routes
    register_routes(app)

    return app
```

**Step 2: Run web tests**

```bash
pytest tests/web/ -v
```

Expected: All PASS

**Step 3: Commit**

```bash
git add web/app.py
git commit -m "feat: read Flask secret key from FLASK_SECRET_KEY env var"
```

---

### Task 7: Create Dockerfile

**Files:**
- Create: `Dockerfile`

**Step 1: Create Dockerfile**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs

EXPOSE 5743

CMD ["python", "main.py"]
```

**Step 2: Build the image to verify it works**

```bash
docker build -t river-monitor:local .
```

Expected: Build completes with no errors. (The app will fail to start without a PostgreSQL instance — that's expected at this stage.)

**Step 3: Commit**

```bash
git add Dockerfile
git commit -m "feat: add Dockerfile for Python 3.11 app container"
```

---

### Task 8: Create docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

**Step 1: Create docker-compose.yml**

```yaml
services:
  app:
    build: .
    ports:
      - "5743:5743"
    environment:
      - DATABASE_URL=postgresql://river:river@db:5432/rivermonitor
      - FLASK_SECRET_KEY=change-me-in-production
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - ./logs:/app/logs
    restart: unless-stopped

  db:
    image: postgres:16
    environment:
      POSTGRES_USER: river
      POSTGRES_PASSWORD: river
      POSTGRES_DB: rivermonitor
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U river -d rivermonitor"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

volumes:
  pgdata:
```

**Step 2: Start the stack**

```bash
docker compose up --build
```

Expected: Both containers start. Visit `http://localhost:5743` — dashboard loads.

**Step 3: Stop and commit**

```bash
docker compose down
git add docker-compose.yml
git commit -m "feat: add docker-compose.yml with app + postgres containers"
```

---

### Task 9: Create .env.example

**Files:**
- Create: `.env.example`
- Create: `.gitignore` entry for `.env`

**Step 1: Create .env.example**

```bash
# River Monitor — environment variables
# Copy to .env and fill in real values

# PostgreSQL (production: replace with managed DB URL)
DATABASE_URL=postgresql://river:river@db:5432/rivermonitor

# Flask
FLASK_SECRET_KEY=change-me-to-a-random-string

# Telegram (leave blank to disable)
# Used by monitor/adapters/telegram.py — also configurable via web portal settings
# The portal stores these in the DB; env vars are the production-preferred approach.

# Twilio SMS (leave blank to disable)
# TWILIO_ACCOUNT_SID=
# TWILIO_AUTH_TOKEN=
# TWILIO_SMS_NUMBER=+1xxxxxxxxxx
# TWILIO_WHATSAPP_NUMBER=whatsapp:+1xxxxxxxxxx

# Facebook Messenger (leave blank to disable)
# FACEBOOK_PAGE_TOKEN=
# FACEBOOK_VERIFY_TOKEN=

# Test database (used by pytest)
# TEST_DATABASE_URL=postgresql://river:river@localhost:5432/river_test
```

**Step 2: Ensure .env is gitignored**

Check if `.gitignore` exists and add `.env` if not already there:

```bash
echo ".env" >> .gitignore
```

**Step 3: Commit**

```bash
git add .env.example .gitignore
git commit -m "chore: add .env.example and gitignore .env"
```

---

### Task 10: Delete obsolete files

**Files to delete:**
- `service.py` — replaced by `main.py` + Docker
- `launch.py` — Windows desktop launcher
- `create_shortcut.py` — Windows .lnk creator
- `setup_wizard.py` — superseded by web portal
- `db/migration.py` — legacy config.py import, never needed in Docker
- `river_monitor.py` — standalone CLI, superseded
- `db/river_monitor.db` — SQLite file (if present; data is now in PostgreSQL)

**Step 1: Delete the files**

```bash
git rm service.py launch.py create_shortcut.py setup_wizard.py db/migration.py river_monitor.py
```

If `db/river_monitor.db` exists:
```bash
rm db/river_monitor.db
echo "db/*.db" >> .gitignore
```

**Step 2: Run the full test suite**

```bash
pytest -v
```

Expected: All PASS (no test imports the deleted files except test_migration.py and test_launcher.py which were already deleted in Task 4)

**Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: delete Windows-service and legacy CLI files"
```

---

### Task 11: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

Update the commands section and architecture description to reflect Docker.

**Step 1: Replace the Commands and Service Commands sections**

Replace the top of CLAUDE.md with:

```markdown
## Commands

```bash
# Install dependencies (for local development / running tests)
pip install -r requirements.txt

# Run tests (requires PostgreSQL — see .env.example for TEST_DATABASE_URL)
pytest

# Run a specific test file
pytest tests/monitor/test_polling.py

# Run locally with Docker (recommended)
docker compose up --build

# Stop
docker compose down
```

## Production deployment

Build and push to GitHub Container Registry:

```bash
docker build -t ghcr.io/<your-org>/river-monitor:latest .
docker push ghcr.io/<your-org>/river-monitor:latest
```

On the server:
```bash
docker compose pull
docker compose up -d
```
```

Also update the Architecture section to remove references to `service.py`, `launch.py`, `create_shortcut.py`, `setup_wizard.py`, `db/migration.py`, and `river_monitor.py`. Update the entry point description to reference `main.py`.

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for Docker-based architecture"
```

---

## Summary of all changed files

| Action | File |
|--------|------|
| Modify | `requirements.txt` |
| Modify | `tests/conftest.py` |
| Modify | `db/models.py` |
| Modify | `tests/db/test_models.py` |
| Modify | `web/app.py` |
| Modify | `CLAUDE.md` |
| Create | `main.py` |
| Create | `Dockerfile` |
| Create | `docker-compose.yml` |
| Create | `.env.example` |
| Delete | `service.py` |
| Delete | `launch.py` |
| Delete | `create_shortcut.py` |
| Delete | `setup_wizard.py` |
| Delete | `db/migration.py` |
| Delete | `river_monitor.py` |
| Delete | `tests/db/test_migration.py` |
| Delete | `tests/test_launcher.py` |
