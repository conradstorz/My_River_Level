# Windows Service + Notification System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform the CLI river monitor into a persistent Windows service with a localhost admin portal and multi-channel (Telegram, WhatsApp, SMS, Facebook) subscriber notification system.

**Architecture:** Single `pywin32` Windows service containing four threads (polling, scheduler, notification dispatcher, Flask web). SQLite is the shared state store. Subscribers register by messaging a bot; notifications fire on state transitions and severity-based reminder cadences.

**Tech Stack:** Python 3.8+, pywin32, Flask, python-telegram-bot v20+, twilio, sqlite3, pytest, pytest-mock

**Design doc:** `docs/plans/2026-03-01-windows-service-design.md`

---

## Phase 1: Foundation

### Task 1: Project Scaffolding

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `monitor/__init__.py`
- Create: `monitor/adapters/__init__.py`
- Create: `web/__init__.py`
- Create: `db/__init__.py`

**Step 1: Create directory structure**

```bash
mkdir -p monitor/adapters web/templates db tests/monitor tests/web tests/db logs
```

**Step 2: Update `requirements.txt`**

Replace the entire file with:

```
# USGS Water Data Tools
dataretrieval
hyswap

# Data Analysis
pandas
numpy

# API and Web Requests
requests

# Visualization (optional)
matplotlib

# Interactive setup
colorama

# Windows Service
pywin32

# Web portal
flask

# Notification channels
python-telegram-bot>=20.0
twilio

# Testing
pytest
pytest-mock
```

**Step 3: Install new dependencies**

```bash
venv\Scripts\activate
pip install -r requirements.txt
```

Expected: all packages install without error.

**Step 4: Create `tests/conftest.py`**

```python
import pytest
import sqlite3
import tempfile
import os

@pytest.fixture
def tmp_db(tmp_path):
    """Provides a temporary SQLite database path."""
    return str(tmp_path / "test.db")
```

**Step 5: Create empty `__init__.py` files**

```bash
touch monitor/__init__.py monitor/adapters/__init__.py web/__init__.py db/__init__.py tests/__init__.py tests/monitor/__init__.py tests/web/__init__.py tests/db/__init__.py
```

**Step 6: Verify pytest runs**

```bash
pytest tests/ -v
```

Expected: "no tests ran" (0 tests collected, no errors).

**Step 7: Commit**

```bash
git add .
git commit -m "feat: scaffold project structure for windows service"
```

---

### Task 2: Database Schema

**Files:**
- Create: `db/models.py`
- Create: `tests/db/test_models.py`

**Step 1: Write failing tests**

Create `tests/db/test_models.py`:

```python
import pytest
import sqlite3
from db.models import init_db, get_setting, set_setting, get_db

def test_init_db_creates_all_tables(tmp_db):
    init_db(tmp_db)
    conn = sqlite3.connect(tmp_db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()
    expected = {"sites", "settings", "site_conditions", "subscribers",
                "notifications", "pending_registrations"}
    assert expected.issubset(tables)

def test_set_and_get_setting(tmp_db):
    init_db(tmp_db)
    set_setting("poll_interval_minutes", "15", tmp_db)
    assert get_setting("poll_interval_minutes", tmp_db) == "15"

def test_get_setting_returns_default_when_missing(tmp_db):
    init_db(tmp_db)
    assert get_setting("nonexistent_key", tmp_db, default="42") == "42"

def test_init_db_seeds_default_settings(tmp_db):
    init_db(tmp_db)
    assert get_setting("poll_interval_minutes", tmp_db) == "15"
    assert get_setting("low_percentile", tmp_db) == "10"
    assert get_setting("high_percentile", tmp_db) == "90"
    assert get_setting("very_low_percentile", tmp_db) == "5"
    assert get_setting("very_high_percentile", tmp_db) == "95"
    assert get_setting("reminder_low_high_hours", tmp_db) == "24"
    assert get_setting("reminder_severe_hours", tmp_db) == "4"
    assert get_setting("historical_start_year", tmp_db) == "1980"
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/db/test_models.py -v
```

Expected: ImportError — `db.models` does not exist yet.

**Step 3: Create `db/models.py`**

```python
import sqlite3
import os

# Default database path — overridden by service.py
DEFAULT_DB = os.path.join(os.path.dirname(__file__), "river_monitor.db")

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
    # Channel credentials — empty until configured via portal
    "telegram_bot_token": "",
    "twilio_account_sid": "",
    "twilio_auth_token": "",
    "twilio_sms_number": "",
    "twilio_whatsapp_number": "",
    "facebook_page_token": "",
    "facebook_verify_token": "",
}

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_number TEXT NOT NULL UNIQUE,
    station_name TEXT NOT NULL DEFAULT '',
    parameter_code TEXT NOT NULL DEFAULT '00060',
    active INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS site_conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id),
    checked_at TEXT NOT NULL DEFAULT (datetime('now')),
    current_value REAL,
    unit TEXT NOT NULL DEFAULT 'cfs',
    percentile REAL,
    severity TEXT NOT NULL DEFAULT 'UNKNOWN'
);

CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    opted_in_at TEXT NOT NULL DEFAULT (datetime('now')),
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(channel, channel_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscriber_id INTEGER REFERENCES subscribers(id),
    site_id INTEGER REFERENCES sites(id),
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    channel TEXT NOT NULL,
    message_text TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 1,
    error_msg TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pending_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(channel, channel_id)
);
"""


def get_db(db_path=None):
    """Return a new SQLite connection. Caller is responsible for closing it."""
    path = db_path or DEFAULT_DB
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path=None):
    """Create all tables and seed default settings."""
    conn = get_db(db_path)
    conn.executescript(SCHEMA)
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
    conn.commit()
    conn.close()


def get_setting(key, db_path=None, default=None):
    conn = get_db(db_path)
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    if row is None:
        return default
    return row["value"]


def set_setting(key, value, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, str(value))
    )
    conn.commit()
    conn.close()
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/db/test_models.py -v
```

Expected: 4 tests PASS.

**Step 5: Commit**

```bash
git add db/models.py tests/db/test_models.py
git commit -m "feat: add sqlite schema and db helper functions"
```

---

### Task 3: Config Migration

**Files:**
- Create: `db/migration.py`
- Create: `tests/db/test_migration.py`

**Step 1: Write failing tests**

Create `tests/db/test_migration.py`:

```python
import pytest
import sys
import types
from db.models import init_db, get_setting, get_db
from db.migration import migrate_from_config

def _make_config(sites=None, lat=None, lon=None, param="00060", start_year=1985):
    mod = types.SimpleNamespace()
    mod.MONITORING_SITES = sites or []
    mod.LOCATION = {"latitude": lat, "longitude": lon}
    mod.PARAMETER_CODE = param
    mod.HISTORICAL_START_YEAR = start_year
    mod.LOW_FLOW_PERCENTILE = 10
    mod.HIGH_FLOW_PERCENTILE = 90
    mod.VERY_LOW_PERCENTILE = 5
    mod.VERY_HIGH_PERCENTILE = 95
    mod.SEARCH_RADIUS_MILES = 30
    return mod

def test_migrate_seeds_sites(tmp_db):
    init_db(tmp_db)
    config = _make_config(sites=["03277200", "03292470"])
    migrate_from_config(config, tmp_db)
    conn = get_db(tmp_db)
    rows = conn.execute("SELECT site_number FROM sites").fetchall()
    conn.close()
    site_numbers = [r["site_number"] for r in rows]
    assert "03277200" in site_numbers
    assert "03292470" in site_numbers

def test_migrate_seeds_settings(tmp_db):
    init_db(tmp_db)
    config = _make_config(start_year=1985)
    migrate_from_config(config, tmp_db)
    assert get_setting("historical_start_year", tmp_db) == "1985"

def test_migrate_skips_duplicate_sites(tmp_db):
    init_db(tmp_db)
    config = _make_config(sites=["03277200"])
    migrate_from_config(config, tmp_db)
    migrate_from_config(config, tmp_db)  # run twice
    conn = get_db(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
    conn.close()
    assert count == 1
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/db/test_migration.py -v
```

Expected: ImportError.

**Step 3: Create `db/migration.py`**

```python
from db.models import get_db, set_setting


def migrate_from_config(config_module, db_path=None):
    """
    Seed the database from a legacy config.py module.
    Safe to call multiple times — uses INSERT OR IGNORE for sites.
    """
    conn = get_db(db_path)

    # Migrate sites
    param = getattr(config_module, "PARAMETER_CODE", "00060")
    for site_number in getattr(config_module, "MONITORING_SITES", []):
        conn.execute(
            "INSERT OR IGNORE INTO sites (site_number, parameter_code) VALUES (?, ?)",
            (site_number, param)
        )

    conn.commit()
    conn.close()

    # Migrate scalar settings
    mapping = {
        "historical_start_year": "HISTORICAL_START_YEAR",
        "low_percentile": "LOW_FLOW_PERCENTILE",
        "high_percentile": "HIGH_FLOW_PERCENTILE",
        "very_low_percentile": "VERY_LOW_PERCENTILE",
        "very_high_percentile": "VERY_HIGH_PERCENTILE",
        "search_radius_miles": "SEARCH_RADIUS_MILES",
    }
    for db_key, config_attr in mapping.items():
        value = getattr(config_module, config_attr, None)
        if value is not None:
            set_setting(db_key, str(value), db_path)
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/db/test_migration.py -v
```

Expected: 3 tests PASS.

**Step 5: Commit**

```bash
git add db/migration.py tests/db/test_migration.py
git commit -m "feat: migrate legacy config.py to sqlite on first run"
```

---

## Phase 2: Core Monitoring

### Task 4: Polling Thread

**Files:**
- Create: `monitor/polling.py`
- Create: `tests/monitor/test_polling.py`

**Step 1: Write failing tests**

Create `tests/monitor/test_polling.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from db.models import init_db, get_db
from monitor.polling import detect_transition, record_condition, get_active_sites

def test_detect_transition_returns_none_when_same_severity(tmp_db):
    assert detect_transition("HIGH", "HIGH") is None

def test_detect_transition_returns_tuple_when_different(tmp_db):
    result = detect_transition("NORMAL", "HIGH")
    assert result == ("NORMAL", "HIGH")

def test_detect_transition_normal_to_severe(tmp_db):
    result = detect_transition("NORMAL", "SEVERE HIGH")
    assert result == ("NORMAL", "SEVERE HIGH")

def test_record_condition_inserts_row(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (site_number) VALUES ('12345678')")
    conn.commit()
    site_id = conn.execute("SELECT id FROM sites WHERE site_number='12345678'").fetchone()["id"]
    conn.close()

    record_condition(site_id, 500.0, "cfs", 45.2, "NORMAL", tmp_db)

    conn = get_db(tmp_db)
    row = conn.execute("SELECT * FROM site_conditions WHERE site_id=?", (site_id,)).fetchone()
    conn.close()
    assert row["current_value"] == 500.0
    assert row["severity"] == "NORMAL"
    assert row["percentile"] == pytest.approx(45.2)

def test_get_active_sites_returns_only_active(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (site_number, active) VALUES ('11111111', 1)")
    conn.execute("INSERT INTO sites (site_number, active) VALUES ('22222222', 0)")
    conn.commit()
    conn.close()

    sites = get_active_sites(tmp_db)
    site_numbers = [s["site_number"] for s in sites]
    assert "11111111" in site_numbers
    assert "22222222" not in site_numbers
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/monitor/test_polling.py -v
```

Expected: ImportError.

**Step 3: Create `monitor/polling.py`**

```python
import threading
import time
import queue
import logging
from datetime import datetime

import pandas as pd
import numpy as np
import dataretrieval.nwis as nwis

from db.models import get_db, get_setting, init_db

logger = logging.getLogger(__name__)


def get_active_sites(db_path=None):
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT id, site_number, station_name, parameter_code FROM sites WHERE active=1"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_previous_severity(site_id, db_path=None):
    conn = get_db(db_path)
    row = conn.execute(
        "SELECT severity FROM site_conditions WHERE site_id=? ORDER BY id DESC LIMIT 1",
        (site_id,)
    ).fetchone()
    conn.close()
    return row["severity"] if row else None


def record_condition(site_id, current_value, unit, percentile, severity, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        """INSERT INTO site_conditions
           (site_id, current_value, unit, percentile, severity)
           VALUES (?, ?, ?, ?, ?)""",
        (site_id, current_value, unit, percentile, severity)
    )
    conn.commit()
    conn.close()


def detect_transition(previous_severity, new_severity):
    """Returns (old, new) tuple if severity changed, else None."""
    if previous_severity == new_severity:
        return None
    return (previous_severity, new_severity)


def classify_condition(percentile, db_path=None):
    very_low = float(get_setting("very_low_percentile", db_path, default="5"))
    low = float(get_setting("low_percentile", db_path, default="10"))
    high = float(get_setting("high_percentile", db_path, default="90"))
    very_high = float(get_setting("very_high_percentile", db_path, default="95"))

    if percentile is None:
        return "UNKNOWN"
    if percentile <= very_low:
        return "SEVERE LOW"
    if percentile <= low:
        return "LOW"
    if percentile >= very_high:
        return "SEVERE HIGH"
    if percentile >= high:
        return "HIGH"
    return "NORMAL"


def fetch_and_evaluate_site(site, db_path=None):
    """
    Fetch USGS data for one site, compute percentile and severity,
    record the condition, and return a transition dict or None.
    """
    site_id = site["id"]
    site_number = site["site_number"]
    param_code = site["parameter_code"]

    try:
        # Current value — last 7 days of interval data
        end = datetime.now()
        start = end.replace(hour=0, minute=0, second=0) - __import__('datetime').timedelta(days=7)
        df_iv, _ = nwis.get_iv(
            sites=site_number,
            parameterCd=param_code,
            start=start.strftime('%Y-%m-%d'),
            end=end.strftime('%Y-%m-%d')
        )
        if df_iv is None or len(df_iv) == 0:
            logger.warning("No interval data for site %s", site_number)
            return None

        param_cols = [c for c in df_iv.columns if c.startswith(param_code)]
        if not param_cols:
            return None
        current_value = pd.to_numeric(df_iv[param_cols[0]].iloc[-1], errors='coerce')
        if pd.isna(current_value) or current_value < 0:
            return None

        # Historical daily values
        start_year = get_setting("historical_start_year", db_path, default="1980")
        df_dv, _ = nwis.get_dv(
            sites=site_number,
            parameterCd=param_code,
            start=f"{start_year}-01-01",
            end=end.strftime('%Y-%m-%d')
        )
        if df_dv is None or len(df_dv) == 0:
            return None

        hist_cols = [c for c in df_dv.columns if param_code in c]
        if not hist_cols:
            return None
        hist_values = pd.to_numeric(df_dv[hist_cols[0]], errors='coerce').values
        hist_values = hist_values[~np.isnan(hist_values) & (hist_values >= 0)]
        if len(hist_values) == 0:
            return None

        percentile = float((hist_values < current_value).sum() / len(hist_values) * 100)
        unit = {"00060": "cfs", "00065": "ft"}.get(param_code, "units")
        severity = classify_condition(percentile, db_path)

        previous_severity = get_previous_severity(site_id, db_path)
        record_condition(site_id, float(current_value), unit, percentile, severity, db_path)

        transition = detect_transition(previous_severity, severity)
        if transition:
            return {
                "site_id": site_id,
                "site_number": site_number,
                "station_name": site["station_name"],
                "previous_severity": transition[0],
                "new_severity": transition[1],
                "current_value": float(current_value),
                "unit": unit,
                "percentile": percentile,
            }
        return None

    except Exception:
        logger.exception("Error evaluating site %s", site_number)
        return None


class PollingThread(threading.Thread):
    def __init__(self, notification_queue, db_path=None, stop_event=None):
        super().__init__(name="PollingThread", daemon=True)
        self.notification_queue = notification_queue
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()

    def run(self):
        logger.info("PollingThread started")
        while not self.stop_event.is_set():
            self._poll()
            interval = int(get_setting("poll_interval_minutes", self.db_path, default="15"))
            self.stop_event.wait(timeout=interval * 60)
        logger.info("PollingThread stopped")

    def _poll(self):
        sites = get_active_sites(self.db_path)
        for site in sites:
            transition = fetch_and_evaluate_site(site, self.db_path)
            if transition:
                self.notification_queue.put({
                    "type": "transition",
                    "data": transition,
                })
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/monitor/test_polling.py -v
```

Expected: 5 tests PASS.

**Step 5: Commit**

```bash
git add monitor/polling.py tests/monitor/test_polling.py
git commit -m "feat: polling thread with state transition detection"
```

---

### Task 5: Scheduler Thread

**Files:**
- Create: `monitor/scheduler.py`
- Create: `tests/monitor/test_scheduler.py`

**Step 1: Write failing tests**

Create `tests/monitor/test_scheduler.py`:

```python
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from db.models import init_db, get_db
from monitor.scheduler import is_reminder_due, get_reminder_interval_hours

def test_get_reminder_interval_hours_low(tmp_db):
    init_db(tmp_db)
    assert get_reminder_interval_hours("LOW", tmp_db) == 24.0

def test_get_reminder_interval_hours_high(tmp_db):
    init_db(tmp_db)
    assert get_reminder_interval_hours("HIGH", tmp_db) == 24.0

def test_get_reminder_interval_hours_severe_low(tmp_db):
    init_db(tmp_db)
    assert get_reminder_interval_hours("SEVERE LOW", tmp_db) == 4.0

def test_get_reminder_interval_hours_severe_high(tmp_db):
    init_db(tmp_db)
    assert get_reminder_interval_hours("SEVERE HIGH", tmp_db) == 4.0

def test_get_reminder_interval_returns_none_for_normal(tmp_db):
    init_db(tmp_db)
    assert get_reminder_interval_hours("NORMAL", tmp_db) is None

def test_is_reminder_due_when_no_previous_notification(tmp_db):
    init_db(tmp_db)
    # No prior notification — reminder is always due
    assert is_reminder_due(site_id=1, severity="HIGH", db_path=tmp_db) is True

def test_is_reminder_due_when_recent_notification(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (site_number) VALUES ('00000001')")
    conn.execute("INSERT INTO subscribers (channel, channel_id) VALUES ('telegram', 'abc')")
    conn.execute(
        """INSERT INTO notifications
           (subscriber_id, site_id, sent_at, channel, message_text, trigger_type)
           VALUES (1, 1, datetime('now', '-1 hour'), 'telegram', 'test', 'reminder')"""
    )
    conn.commit()
    conn.close()
    # 1 hour ago — not due yet (interval is 4h for SEVERE)
    assert is_reminder_due(site_id=1, severity="SEVERE HIGH", db_path=tmp_db) is False

def test_is_reminder_due_when_old_notification(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (site_number) VALUES ('00000001')")
    conn.execute("INSERT INTO subscribers (channel, channel_id) VALUES ('telegram', 'abc')")
    conn.execute(
        """INSERT INTO notifications
           (subscriber_id, site_id, sent_at, channel, message_text, trigger_type)
           VALUES (1, 1, datetime('now', '-25 hours'), 'telegram', 'test', 'reminder')"""
    )
    conn.commit()
    conn.close()
    # 25 hours ago — due (interval is 24h for HIGH)
    assert is_reminder_due(site_id=1, severity="HIGH", db_path=tmp_db) is True
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/monitor/test_scheduler.py -v
```

Expected: ImportError.

**Step 3: Create `monitor/scheduler.py`**

```python
import threading
import time
import logging

from db.models import get_db, get_setting

logger = logging.getLogger(__name__)


def get_reminder_interval_hours(severity, db_path=None):
    """Return reminder interval in hours for the given severity, or None if no reminder."""
    if severity in ("SEVERE LOW", "SEVERE HIGH"):
        return float(get_setting("reminder_severe_hours", db_path, default="4"))
    if severity in ("LOW", "HIGH"):
        return float(get_setting("reminder_low_high_hours", db_path, default="24"))
    return None


def is_reminder_due(site_id, severity, db_path=None):
    """Return True if a reminder should fire for this site at this severity level."""
    interval_hours = get_reminder_interval_hours(severity, db_path)
    if interval_hours is None:
        return False

    conn = get_db(db_path)
    row = conn.execute(
        """SELECT sent_at FROM notifications
           WHERE site_id = ? AND trigger_type = 'reminder'
           ORDER BY sent_at DESC LIMIT 1""",
        (site_id,)
    ).fetchone()
    conn.close()

    if row is None:
        return True

    from datetime import datetime, timedelta
    last_sent = datetime.fromisoformat(row["sent_at"])
    return datetime.now() - last_sent >= timedelta(hours=interval_hours)


def get_current_site_severities(db_path=None):
    """Return list of {site_id, site_number, station_name, severity} for all active sites."""
    conn = get_db(db_path)
    rows = conn.execute(
        """SELECT s.id AS site_id, s.site_number, s.station_name,
                  sc.severity, sc.current_value, sc.unit, sc.percentile
           FROM sites s
           JOIN site_conditions sc ON sc.id = (
               SELECT id FROM site_conditions
               WHERE site_id = s.id ORDER BY id DESC LIMIT 1
           )
           WHERE s.active = 1"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


class SchedulerThread(threading.Thread):
    CHECK_INTERVAL_SECONDS = 300  # check every 5 minutes

    def __init__(self, notification_queue, db_path=None, stop_event=None):
        super().__init__(name="SchedulerThread", daemon=True)
        self.notification_queue = notification_queue
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()

    def run(self):
        logger.info("SchedulerThread started")
        while not self.stop_event.is_set():
            self._check_reminders()
            self.stop_event.wait(timeout=self.CHECK_INTERVAL_SECONDS)
        logger.info("SchedulerThread stopped")

    def _check_reminders(self):
        for site in get_current_site_severities(self.db_path):
            if is_reminder_due(site["site_id"], site["severity"], self.db_path):
                self.notification_queue.put({
                    "type": "reminder",
                    "data": site,
                })
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/monitor/test_scheduler.py -v
```

Expected: 8 tests PASS.

**Step 5: Commit**

```bash
git add monitor/scheduler.py tests/monitor/test_scheduler.py
git commit -m "feat: scheduler thread with severity-based reminder cadences"
```

---

### Task 6: Notification Dispatcher

**Files:**
- Create: `monitor/dispatcher.py`
- Create: `tests/monitor/test_dispatcher.py`

**Step 1: Write failing tests**

Create `tests/monitor/test_dispatcher.py`:

```python
import pytest
import queue
from unittest.mock import MagicMock, patch
from db.models import init_db, get_db
from monitor.dispatcher import NotificationDispatcher, format_transition_message, format_reminder_message

def test_format_transition_message_normal_to_high():
    data = {
        "station_name": "Test Creek",
        "site_number": "12345678",
        "previous_severity": "NORMAL",
        "new_severity": "HIGH",
        "current_value": 1500.0,
        "unit": "cfs",
        "percentile": 91.2,
    }
    msg = format_transition_message(data)
    assert "Test Creek" in msg
    assert "HIGH" in msg
    assert "NORMAL" in msg
    assert "1500" in msg

def test_format_reminder_message():
    data = {
        "station_name": "Test Creek",
        "site_number": "12345678",
        "severity": "SEVERE HIGH",
        "current_value": 9000.0,
        "unit": "cfs",
        "percentile": 97.1,
    }
    msg = format_reminder_message(data)
    assert "Test Creek" in msg
    assert "SEVERE HIGH" in msg

def test_dispatcher_calls_adapter_for_each_subscriber(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (id, site_number) VALUES (1, '12345678')")
    conn.execute("INSERT INTO subscribers (channel, channel_id, active) VALUES ('telegram', 'chat1', 1)")
    conn.commit()
    conn.close()

    mock_adapter = MagicMock()
    mock_adapter.channel = "telegram"
    mock_adapter.send.return_value = True

    q = queue.Queue()
    q.put({"type": "transition", "data": {
        "site_id": 1, "site_number": "12345678", "station_name": "Test",
        "previous_severity": "NORMAL", "new_severity": "HIGH",
        "current_value": 1500.0, "unit": "cfs", "percentile": 91.0,
    }})
    q.put(None)  # poison pill to stop

    dispatcher = NotificationDispatcher(q, adapters=[mock_adapter], db_path=tmp_db)
    dispatcher.run_once()

    mock_adapter.send.assert_called_once()
    args = mock_adapter.send.call_args[0]
    assert args[0] == "chat1"
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/monitor/test_dispatcher.py -v
```

Expected: ImportError.

**Step 3: Create `monitor/dispatcher.py`**

```python
import threading
import queue
import logging

from db.models import get_db

logger = logging.getLogger(__name__)


def format_transition_message(data):
    return (
        f"⚠️ River Level Change: {data['station_name']} (#{data['site_number']})\n"
        f"Condition changed: {data['previous_severity']} → {data['new_severity']}\n"
        f"Current level: {data['current_value']:.2f} {data['unit']} "
        f"({data['percentile']:.1f}th percentile)"
    )


def format_reminder_message(data):
    return (
        f"🔔 River Level Reminder: {data['station_name']} (#{data['site_number']})\n"
        f"Current condition: {data['severity']}\n"
        f"Level: {data['current_value']:.2f} {data['unit']} "
        f"({data['percentile']:.1f}th percentile)"
    )


def get_active_subscribers(db_path=None):
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT id, channel, channel_id FROM subscribers WHERE active=1"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_notification(subscriber_id, site_id, channel, message, trigger_type, success, error_msg="", db_path=None):
    conn = get_db(db_path)
    conn.execute(
        """INSERT INTO notifications
           (subscriber_id, site_id, channel, message_text, trigger_type, success, error_msg)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (subscriber_id, site_id, channel, message, trigger_type, 1 if success else 0, error_msg)
    )
    conn.commit()
    conn.close()


class NotificationDispatcher(threading.Thread):
    def __init__(self, notification_queue, adapters=None, db_path=None, stop_event=None):
        super().__init__(name="NotificationDispatcher", daemon=True)
        self.queue = notification_queue
        self.adapters = {a.channel: a for a in (adapters or [])}
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()

    def run(self):
        logger.info("NotificationDispatcher started")
        while not self.stop_event.is_set():
            self.run_once()
        logger.info("NotificationDispatcher stopped")

    def run_once(self):
        """Process one item from the queue (blocking with 1s timeout)."""
        try:
            item = self.queue.get(timeout=1)
        except queue.Empty:
            return

        if item is None:
            return

        try:
            if item["type"] == "transition":
                message = format_transition_message(item["data"])
                trigger_type = "transition"
                site_id = item["data"]["site_id"]
            elif item["type"] == "reminder":
                message = format_reminder_message(item["data"])
                trigger_type = "reminder"
                site_id = item["data"]["site_id"]
            else:
                return

            subscribers = get_active_subscribers(self.db_path)
            for sub in subscribers:
                adapter = self.adapters.get(sub["channel"])
                if adapter is None:
                    continue
                try:
                    success = adapter.send(sub["channel_id"], message)
                    log_notification(sub["id"], site_id, sub["channel"],
                                     message, trigger_type, success, db_path=self.db_path)
                except Exception as e:
                    logger.exception("Failed to send to %s/%s", sub["channel"], sub["channel_id"])
                    log_notification(sub["id"], site_id, sub["channel"],
                                     message, trigger_type, False, str(e), db_path=self.db_path)
        except Exception:
            logger.exception("Error processing notification item")
        finally:
            self.queue.task_done()
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/monitor/test_dispatcher.py -v
```

Expected: 3 tests PASS.

**Step 5: Commit**

```bash
git add monitor/dispatcher.py tests/monitor/test_dispatcher.py
git commit -m "feat: notification dispatcher with message formatting and logging"
```

---

## Phase 3: Channel Adapters

### Task 7: Telegram Adapter

**Files:**
- Create: `monitor/adapters/telegram.py`

> The Telegram adapter runs its own asyncio event loop in a thread. There are no unit tests for it because all behavior requires a live bot token and network. Manual testing procedure is described below.

**Step 1: Create `monitor/adapters/telegram.py`**

```python
import asyncio
import threading
import logging

from db.models import get_db, get_setting

logger = logging.getLogger(__name__)

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


class TelegramAdapter(threading.Thread):
    channel = "telegram"

    def __init__(self, db_path=None, stop_event=None):
        super().__init__(name="TelegramAdapter", daemon=True)
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()
        self._app = None
        self._loop = None

    # ── Outbound ──────────────────────────────────────────────────────────────

    def send(self, chat_id, message):
        """Send a message to a chat_id. Called from NotificationDispatcher thread."""
        if self._loop is None or self._app is None:
            logger.warning("Telegram adapter not ready")
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._app.bot.send_message(chat_id=chat_id, text=message),
            self._loop
        )
        try:
            future.result(timeout=10)
            return True
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return False

    # ── Inbound (bot handlers) ────────────────────────────────────────────────

    async def _handle_start(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        chat_id = str(update.effective_chat.id)
        conn = get_db(self.db_path)
        conn.execute(
            "INSERT OR IGNORE INTO pending_registrations (channel, channel_id) VALUES ('telegram', ?)",
            (chat_id,)
        )
        conn.commit()
        conn.close()
        await update.message.reply_text(
            "Welcome to the River Level Monitor!\n"
            "Type /subscribe to receive river condition alerts."
        )

    async def _handle_subscribe(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        chat_id = str(update.effective_chat.id)
        name = update.effective_user.full_name or "Telegram User"
        conn = get_db(self.db_path)
        conn.execute(
            "INSERT OR IGNORE INTO subscribers (display_name, channel, channel_id) VALUES (?, 'telegram', ?)",
            (name, chat_id)
        )
        # Activate if they previously unsubscribed
        conn.execute(
            "UPDATE subscribers SET active=1, display_name=? WHERE channel='telegram' AND channel_id=?",
            (name, chat_id)
        )
        conn.execute(
            "DELETE FROM pending_registrations WHERE channel='telegram' AND channel_id=?",
            (chat_id,)
        )
        conn.commit()
        conn.close()
        await update.message.reply_text("✓ You are now subscribed to river level alerts.")

    async def _handle_unsubscribe(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        chat_id = str(update.effective_chat.id)
        conn = get_db(self.db_path)
        conn.execute(
            "UPDATE subscribers SET active=0 WHERE channel='telegram' AND channel_id=?",
            (chat_id,)
        )
        conn.commit()
        conn.close()
        await update.message.reply_text("You have been unsubscribed.")

    # ── Thread entry point ────────────────────────────────────────────────────

    def run(self):
        if not TELEGRAM_AVAILABLE:
            logger.error("python-telegram-bot not installed")
            return

        token = get_setting("telegram_bot_token", self.db_path)
        if not token:
            logger.warning("Telegram bot token not configured — adapter disabled")
            return

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._app = (
            Application.builder()
            .token(token)
            .build()
        )
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("subscribe", self._handle_subscribe))
        self._app.add_handler(CommandHandler("unsubscribe", self._handle_unsubscribe))

        logger.info("TelegramAdapter started polling")
        self._app.run_polling(stop_signals=None)
        logger.info("TelegramAdapter stopped")

    def stop(self):
        if self._app and self._loop:
            asyncio.run_coroutine_threadsafe(self._app.stop(), self._loop)
```

**Step 2: Manual test procedure**

Once a bot token is configured via the Settings portal:
1. Confirm the service starts without error
2. Message the bot `/start` — expect a welcome reply
3. Message `/subscribe` — expect a confirmation reply
4. Check Subscribers page in the portal — the subscriber should appear

**Step 3: Commit**

```bash
git add monitor/adapters/telegram.py
git commit -m "feat: telegram adapter with /start /subscribe /unsubscribe handlers"
```

---

### Task 8: Twilio Adapters (SMS + WhatsApp)

**Files:**
- Create: `monitor/adapters/sms.py`
- Create: `monitor/adapters/whatsapp.py`

> Both adapters are thin wrappers around the Twilio REST API. Inbound opt-in comes via Flask webhooks (Task 12). These classes only handle outbound sending.

**Step 1: Create `monitor/adapters/sms.py`**

```python
import logging
from db.models import get_setting

logger = logging.getLogger(__name__)

try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False


class SMSAdapter:
    channel = "sms"

    def __init__(self, db_path=None):
        self.db_path = db_path

    def _get_client(self):
        sid = get_setting("twilio_account_sid", self.db_path)
        token = get_setting("twilio_auth_token", self.db_path)
        if not sid or not token:
            return None, None
        return Client(sid, token), get_setting("twilio_sms_number", self.db_path)

    def send(self, to_number, message):
        if not TWILIO_AVAILABLE:
            logger.error("twilio not installed")
            return False
        client, from_number = self._get_client()
        if client is None:
            logger.warning("Twilio credentials not configured")
            return False
        try:
            client.messages.create(body=message, from_=from_number, to=to_number)
            return True
        except Exception as e:
            logger.error("SMS send failed to %s: %s", to_number, e)
            return False
```

**Step 2: Create `monitor/adapters/whatsapp.py`**

```python
import logging
from db.models import get_setting

logger = logging.getLogger(__name__)

try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False


class WhatsAppAdapter:
    channel = "whatsapp"

    def __init__(self, db_path=None):
        self.db_path = db_path

    def _get_client(self):
        sid = get_setting("twilio_account_sid", self.db_path)
        token = get_setting("twilio_auth_token", self.db_path)
        if not sid or not token:
            return None, None
        number = get_setting("twilio_whatsapp_number", self.db_path)
        return Client(sid, token), f"whatsapp:{number}"

    def send(self, to_number, message):
        """to_number should be in format '+15551234567' (no whatsapp: prefix)."""
        if not TWILIO_AVAILABLE:
            logger.error("twilio not installed")
            return False
        client, from_number = self._get_client()
        if client is None:
            logger.warning("Twilio credentials not configured")
            return False
        try:
            client.messages.create(
                body=message,
                from_=from_number,
                to=f"whatsapp:{to_number}"
            )
            return True
        except Exception as e:
            logger.error("WhatsApp send failed to %s: %s", to_number, e)
            return False
```

**Step 3: Commit**

```bash
git add monitor/adapters/sms.py monitor/adapters/whatsapp.py
git commit -m "feat: twilio SMS and WhatsApp outbound adapters"
```

---

### Task 9: Facebook Messenger Adapter

**Files:**
- Create: `monitor/adapters/facebook.py`

**Step 1: Create `monitor/adapters/facebook.py`**

```python
import logging
import requests as req
from db.models import get_setting

logger = logging.getLogger(__name__)

SEND_API_URL = "https://graph.facebook.com/v19.0/me/messages"


class FacebookAdapter:
    channel = "facebook"

    def __init__(self, db_path=None):
        self.db_path = db_path

    def send(self, psid, message):
        """
        psid: Page-Scoped User ID (the subscriber's Facebook ID)
        """
        token = get_setting("facebook_page_token", self.db_path)
        if not token:
            logger.warning("Facebook page token not configured")
            return False
        try:
            response = req.post(
                SEND_API_URL,
                params={"access_token": token},
                json={
                    "recipient": {"id": psid},
                    "message": {"text": message}
                },
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error("Facebook send failed to psid %s: %s", psid, e)
            return False
```

**Step 2: Commit**

```bash
git add monitor/adapters/facebook.py
git commit -m "feat: facebook messenger outbound adapter"
```

---

## Phase 4: Web Portal

### Task 10: Flask App Factory + Base Template

**Files:**
- Create: `web/app.py`
- Create: `web/templates/base.html`
- Create: `tests/web/test_app.py`

**Step 1: Write failing tests**

Create `tests/web/test_app.py`:

```python
import pytest
from db.models import init_db
from web.app import create_app

@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_dashboard_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200

def test_subscribers_page_returns_200(client):
    response = client.get("/subscribers")
    assert response.status_code == 200

def test_sites_page_returns_200(client):
    response = client.get("/sites")
    assert response.status_code == 200

def test_settings_page_returns_200(client):
    response = client.get("/settings")
    assert response.status_code == 200

def test_broadcast_page_returns_200(client):
    response = client.get("/broadcast")
    assert response.status_code == 200
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/web/test_app.py -v
```

Expected: ImportError.

**Step 3: Create `web/app.py`**

```python
from flask import Flask
from db.models import DEFAULT_DB


def create_app(db_path=None, notification_queue=None):
    app = Flask(__name__, template_folder="templates")
    app.config["DB_PATH"] = db_path or DEFAULT_DB
    app.config["NOTIFICATION_QUEUE"] = notification_queue

    from web.routes import register_routes
    register_routes(app)

    return app
```

**Step 4: Create `web/routes.py`** (stub — full routes come in later tasks)

```python
from flask import render_template, current_app


def register_routes(app):
    @app.route("/")
    def dashboard():
        return render_template("dashboard.html")

    @app.route("/subscribers")
    def subscribers():
        return render_template("subscribers.html")

    @app.route("/sites")
    def sites():
        return render_template("sites.html")

    @app.route("/settings")
    def settings():
        return render_template("settings.html")

    @app.route("/broadcast")
    def broadcast():
        return render_template("broadcast.html")
```

**Step 5: Create `web/templates/base.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>River Monitor — {% block title %}{% endblock %}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  {% block head %}{% endblock %}
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-dark bg-primary">
  <div class="container-fluid">
    <a class="navbar-brand" href="/">🌊 River Monitor</a>
    <div class="navbar-nav">
      <a class="nav-link" href="/">Dashboard</a>
      <a class="nav-link" href="/subscribers">Subscribers</a>
      <a class="nav-link" href="/sites">Sites</a>
      <a class="nav-link" href="/settings">Settings</a>
      <a class="nav-link" href="/broadcast">Broadcast</a>
    </div>
  </div>
</nav>
<div class="container mt-4">
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% for category, message in messages %}
      <div class="alert alert-{{ category }}">{{ message }}</div>
    {% endfor %}
  {% endwith %}
  {% block content %}{% endblock %}
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
```

**Step 6: Create stub templates for the 5 pages**

Create `web/templates/dashboard.html`:
```html
{% extends "base.html" %}
{% block title %}Dashboard{% endblock %}
{% block content %}<h1>Dashboard</h1>{% endblock %}
```

Create `web/templates/subscribers.html`:
```html
{% extends "base.html" %}
{% block title %}Subscribers{% endblock %}
{% block content %}<h1>Subscribers</h1>{% endblock %}
```

Create `web/templates/sites.html`:
```html
{% extends "base.html" %}
{% block title %}Sites{% endblock %}
{% block content %}<h1>Sites</h1>{% endblock %}
```

Create `web/templates/settings.html`:
```html
{% extends "base.html" %}
{% block title %}Settings{% endblock %}
{% block content %}<h1>Settings</h1>{% endblock %}
```

Create `web/templates/broadcast.html`:
```html
{% extends "base.html" %}
{% block title %}Broadcast{% endblock %}
{% block content %}<h1>Broadcast</h1>{% endblock %}
```

**Step 7: Run tests to verify they pass**

```bash
pytest tests/web/test_app.py -v
```

Expected: 5 tests PASS.

**Step 8: Commit**

```bash
git add web/ tests/web/
git commit -m "feat: flask app factory and base template with stub routes"
```

---

### Task 11: Dashboard Page

**Files:**
- Modify: `web/routes.py`
- Modify: `web/templates/dashboard.html`
- Create: `tests/web/test_dashboard.py`

**Step 1: Write failing tests**

Create `tests/web/test_dashboard.py`:

```python
import pytest
from db.models import init_db, get_db
from web.app import create_app

@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_dashboard_shows_no_sites_message_when_empty(client):
    response = client.get("/")
    assert b"No sites" in response.data or b"no sites" in response.data.lower()

def test_dashboard_shows_site_condition(client, tmp_db):
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (id, site_number, station_name, active) VALUES (1, '03277200', 'Test Creek', 1)")
    conn.execute("INSERT INTO site_conditions (site_id, current_value, unit, percentile, severity) VALUES (1, 500.0, 'cfs', 45.0, 'NORMAL')")
    conn.commit()
    conn.close()

    response = client.get("/")
    assert b"Test Creek" in response.data
    assert b"NORMAL" in response.data

def test_dashboard_shows_recent_notifications(client, tmp_db):
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (id, site_number, station_name) VALUES (1, '03277200', 'Test Creek')")
    conn.execute("INSERT INTO subscribers (id, channel, channel_id) VALUES (1, 'telegram', 'abc')")
    conn.execute("""INSERT INTO notifications (subscriber_id, site_id, channel, message_text, trigger_type)
                    VALUES (1, 1, 'telegram', 'Test alert message', 'transition')""")
    conn.commit()
    conn.close()

    response = client.get("/")
    assert b"Test alert message" in response.data
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/web/test_dashboard.py -v
```

Expected: FAIL (stub template has no data).

**Step 3: Update dashboard route in `web/routes.py`**

Replace the `dashboard` function:

```python
@app.route("/")
def dashboard():
    db_path = current_app.config["DB_PATH"]
    conn = get_db(db_path)

    # Latest condition per active site
    sites = conn.execute("""
        SELECT s.site_number, s.station_name, sc.current_value, sc.unit,
               sc.percentile, sc.severity, sc.checked_at
        FROM sites s
        LEFT JOIN site_conditions sc ON sc.id = (
            SELECT id FROM site_conditions WHERE site_id = s.id ORDER BY id DESC LIMIT 1
        )
        WHERE s.active = 1
        ORDER BY s.station_name
    """).fetchall()

    recent_notifications = conn.execute("""
        SELECT n.sent_at, n.channel, n.message_text, n.trigger_type, n.success,
               s.station_name
        FROM notifications n
        LEFT JOIN sites s ON s.id = n.site_id
        ORDER BY n.sent_at DESC LIMIT 20
    """).fetchall()

    conn.close()
    return render_template("dashboard.html", sites=sites, recent_notifications=recent_notifications)
```

Add `from db.models import get_db` to imports in `web/routes.py`.

**Step 4: Update `web/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}Dashboard{% endblock %}
{% block head %}<meta http-equiv="refresh" content="60">{% endblock %}
{% block content %}
<h1 class="mb-4">Dashboard</h1>

<h4>Monitored Sites</h4>
{% if sites %}
<table class="table table-striped">
  <thead><tr>
    <th>Station</th><th>Site #</th><th>Level</th><th>Percentile</th>
    <th>Condition</th><th>Last Updated</th>
  </tr></thead>
  <tbody>
  {% for s in sites %}
  {% set badge = "success" if s.severity == "NORMAL"
                 else "warning" if s.severity in ("LOW","HIGH")
                 else "danger" if s.severity in ("SEVERE LOW","SEVERE HIGH")
                 else "secondary" %}
  <tr>
    <td>{{ s.station_name }}</td>
    <td>{{ s.site_number }}</td>
    <td>{{ "%.2f"|format(s.current_value) if s.current_value else "—" }} {{ s.unit or "" }}</td>
    <td>{{ "%.1f"|format(s.percentile) if s.percentile else "—" }}%</td>
    <td><span class="badge bg-{{ badge }}">{{ s.severity or "No data" }}</span></td>
    <td>{{ s.checked_at or "—" }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="text-muted">No sites configured. <a href="/sites">Add a site</a>.</p>
{% endif %}

<h4 class="mt-4">Recent Notifications</h4>
{% if recent_notifications %}
<table class="table table-sm">
  <thead><tr><th>Time</th><th>Station</th><th>Channel</th><th>Type</th><th>Message</th><th>OK</th></tr></thead>
  <tbody>
  {% for n in recent_notifications %}
  <tr>
    <td>{{ n.sent_at }}</td>
    <td>{{ n.station_name or "—" }}</td>
    <td>{{ n.channel }}</td>
    <td>{{ n.trigger_type }}</td>
    <td>{{ n.message_text[:80] }}{% if n.message_text|length > 80 %}…{% endif %}</td>
    <td>{% if n.success %}✓{% else %}<span class="text-danger">✗</span>{% endif %}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="text-muted">No notifications sent yet.</p>
{% endif %}
{% endblock %}
```

**Step 5: Run tests to verify they pass**

```bash
pytest tests/web/test_dashboard.py -v
```

Expected: 3 tests PASS.

**Step 6: Commit**

```bash
git add web/routes.py web/templates/dashboard.html tests/web/test_dashboard.py
git commit -m "feat: dashboard page with site conditions and notification history"
```

---

### Task 12: Subscribers Page + Inbound Webhooks

**Files:**
- Modify: `web/routes.py`
- Modify: `web/templates/subscribers.html`
- Create: `tests/web/test_subscribers.py`

**Step 1: Write failing tests**

Create `tests/web/test_subscribers.py`:

```python
import pytest
from db.models import init_db, get_db
from web.app import create_app

@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_subscribers_page_lists_subscribers(client, tmp_db):
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO subscribers (display_name, channel, channel_id) VALUES ('Alice', 'telegram', '1234')")
    conn.commit()
    conn.close()
    response = client.get("/subscribers")
    assert b"Alice" in response.data

def test_add_subscriber_post(client):
    response = client.post("/subscribers/add", data={
        "display_name": "Bob",
        "channel": "sms",
        "channel_id": "+15551234567"
    }, follow_redirects=True)
    assert response.status_code == 200

def test_remove_subscriber(client, tmp_db):
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO subscribers (id, display_name, channel, channel_id) VALUES (1, 'Alice', 'telegram', '1234')")
    conn.commit()
    conn.close()
    response = client.post("/subscribers/1/remove", follow_redirects=True)
    assert response.status_code == 200
    conn = get_db(tmp_db)
    row = conn.execute("SELECT active FROM subscribers WHERE id=1").fetchone()
    conn.close()
    assert row["active"] == 0

def test_twilio_webhook_registers_sms_subscriber(client):
    response = client.post("/webhook/twilio", data={
        "From": "+15559876543",
        "Body": "JOIN",
        "To": "+15550000000",
    })
    assert response.status_code == 200

def test_facebook_webhook_verification(client, tmp_db):
    from db.models import set_setting
    set_setting("facebook_verify_token", "mytoken", tmp_db)
    response = client.get("/webhook/facebook", query_string={
        "hub.mode": "subscribe",
        "hub.verify_token": "mytoken",
        "hub.challenge": "abc123"
    })
    assert response.status_code == 200
    assert b"abc123" in response.data
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/web/test_subscribers.py -v
```

Expected: FAIL (routes not yet implemented).

**Step 3: Add subscriber routes to `web/routes.py`**

Add these routes inside `register_routes(app)`:

```python
from flask import request, redirect, url_for, flash
import re

@app.route("/subscribers")
def subscribers():
    db_path = current_app.config["DB_PATH"]
    conn = get_db(db_path)
    subs = conn.execute("SELECT * FROM subscribers ORDER BY opted_in_at DESC").fetchall()
    conn.close()
    return render_template("subscribers.html", subscribers=subs)

@app.route("/subscribers/add", methods=["POST"])
def add_subscriber():
    db_path = current_app.config["DB_PATH"]
    display_name = request.form.get("display_name", "").strip()
    channel = request.form.get("channel", "").strip()
    channel_id = request.form.get("channel_id", "").strip()
    if channel and channel_id:
        conn = get_db(db_path)
        conn.execute(
            "INSERT OR REPLACE INTO subscribers (display_name, channel, channel_id, active) VALUES (?,?,?,1)",
            (display_name, channel, channel_id)
        )
        conn.commit()
        conn.close()
        flash("Subscriber added.", "success")
    else:
        flash("Channel and channel ID are required.", "danger")
    return redirect(url_for("subscribers"))

@app.route("/subscribers/<int:sub_id>/remove", methods=["POST"])
def remove_subscriber(sub_id):
    db_path = current_app.config["DB_PATH"]
    conn = get_db(db_path)
    conn.execute("UPDATE subscribers SET active=0 WHERE id=?", (sub_id,))
    conn.commit()
    conn.close()
    flash("Subscriber removed.", "success")
    return redirect(url_for("subscribers"))

# ── Inbound webhooks ─────────────────────────────────────────────────────────

@app.route("/webhook/twilio", methods=["POST"])
def webhook_twilio():
    """Handle inbound SMS and WhatsApp opt-ins from Twilio."""
    from_number = request.form.get("From", "")
    body = request.form.get("Body", "").strip().upper()
    to_number = request.form.get("To", "")

    db_path = current_app.config["DB_PATH"]

    # Determine channel by checking if the To number is the WhatsApp number
    wa_number = get_setting("twilio_whatsapp_number", db_path)
    channel = "whatsapp" if wa_number and wa_number in to_number else "sms"
    # Strip whatsapp: prefix if present
    clean_from = from_number.replace("whatsapp:", "")

    conn = get_db(db_path)
    if body == "JOIN":
        conn.execute(
            "INSERT OR REPLACE INTO subscribers (display_name, channel, channel_id, active) VALUES (?,?,?,1)",
            (clean_from, channel, clean_from)
        )
        conn.commit()
    elif body in ("STOP", "UNSUBSCRIBE"):
        conn.execute(
            "UPDATE subscribers SET active=0 WHERE channel=? AND channel_id=?",
            (channel, clean_from)
        )
        conn.commit()
    conn.close()

    # Twilio expects a TwiML response (can be empty)
    return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>', 200, {"Content-Type": "text/xml"}

@app.route("/webhook/facebook", methods=["GET", "POST"])
def webhook_facebook():
    db_path = current_app.config["DB_PATH"]
    if request.method == "GET":
        # Webhook verification
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        verify_token = get_setting("facebook_verify_token", db_path)
        if mode == "subscribe" and token == verify_token:
            return challenge, 200
        return "Forbidden", 403

    # Inbound message
    data = request.get_json(force=True, silent=True) or {}
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            psid = event.get("sender", {}).get("id")
            text = event.get("message", {}).get("text", "").strip().upper()
            if psid and text == "JOIN":
                conn = get_db(db_path)
                conn.execute(
                    "INSERT OR REPLACE INTO subscribers (display_name, channel, channel_id, active) VALUES (?,?,?,1)",
                    (psid, "facebook", psid)
                )
                conn.commit()
                conn.close()
    return "OK", 200
```

Add `from db.models import get_db, get_setting` at the top of `web/routes.py`.

**Step 4: Update `web/templates/subscribers.html`**

```html
{% extends "base.html" %}
{% block title %}Subscribers{% endblock %}
{% block content %}
<h1 class="mb-4">Subscribers</h1>

<h4>Add Subscriber Manually</h4>
<form method="post" action="/subscribers/add" class="row g-2 mb-4">
  <div class="col-md-3"><input name="display_name" class="form-control" placeholder="Name"></div>
  <div class="col-md-2">
    <select name="channel" class="form-select">
      <option value="telegram">Telegram</option>
      <option value="sms">SMS</option>
      <option value="whatsapp">WhatsApp</option>
      <option value="facebook">Facebook</option>
    </select>
  </div>
  <div class="col-md-4"><input name="channel_id" class="form-control" placeholder="Chat ID / Phone / PSID"></div>
  <div class="col-md-2"><button class="btn btn-primary w-100">Add</button></div>
</form>

<h4>Active Subscribers ({{ subscribers|selectattr("active","equalto",1)|list|length }})</h4>
{% if subscribers %}
<table class="table table-striped">
  <thead><tr><th>Name</th><th>Channel</th><th>ID</th><th>Opted In</th><th></th></tr></thead>
  <tbody>
  {% for s in subscribers if s.active %}
  <tr>
    <td>{{ s.display_name or "—" }}</td>
    <td><span class="badge bg-secondary">{{ s.channel }}</span></td>
    <td><code>{{ s.channel_id }}</code></td>
    <td>{{ s.opted_in_at }}</td>
    <td>
      <form method="post" action="/subscribers/{{ s.id }}/remove" style="display:inline">
        <button class="btn btn-sm btn-outline-danger">Remove</button>
      </form>
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="text-muted">No subscribers yet. They can opt in by messaging a bot.</p>
{% endif %}
{% endblock %}
```

**Step 5: Run tests to verify they pass**

```bash
pytest tests/web/test_subscribers.py -v
```

Expected: 5 tests PASS.

**Step 6: Commit**

```bash
git add web/routes.py web/templates/subscribers.html tests/web/test_subscribers.py
git commit -m "feat: subscribers page with add/remove and twilio/facebook webhooks"
```

---

### Task 13: Sites Page

**Files:**
- Modify: `web/routes.py`
- Modify: `web/templates/sites.html`
- Create: `tests/web/test_sites.py`

**Step 1: Write failing tests**

Create `tests/web/test_sites.py`:

```python
import pytest
from db.models import init_db, get_db
from web.app import create_app

@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_sites_page_lists_sites(client, tmp_db):
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (site_number, station_name) VALUES ('03277200', 'Salt River')")
    conn.commit()
    conn.close()
    response = client.get("/sites")
    assert b"Salt River" in response.data

def test_add_site_post(client):
    response = client.post("/sites/add", data={
        "site_number": "03277200",
        "station_name": "Salt River",
        "parameter_code": "00065",
    }, follow_redirects=True)
    assert response.status_code == 200

def test_toggle_site_active(client, tmp_db):
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (id, site_number, active) VALUES (1, '03277200', 1)")
    conn.commit()
    conn.close()
    client.post("/sites/1/toggle")
    conn = get_db(tmp_db)
    row = conn.execute("SELECT active FROM sites WHERE id=1").fetchone()
    conn.close()
    assert row["active"] == 0
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/web/test_sites.py -v
```

Expected: FAIL.

**Step 3: Add sites routes to `web/routes.py`**

```python
@app.route("/sites")
def sites():
    db_path = current_app.config["DB_PATH"]
    conn = get_db(db_path)
    all_sites = conn.execute("SELECT * FROM sites ORDER BY station_name").fetchall()
    conn.close()
    return render_template("sites.html", sites=all_sites)

@app.route("/sites/add", methods=["POST"])
def add_site():
    db_path = current_app.config["DB_PATH"]
    site_number = request.form.get("site_number", "").strip()
    station_name = request.form.get("station_name", "").strip()
    param_code = request.form.get("parameter_code", "00060").strip()
    if site_number:
        conn = get_db(db_path)
        conn.execute(
            "INSERT OR IGNORE INTO sites (site_number, station_name, parameter_code) VALUES (?,?,?)",
            (site_number, station_name, param_code)
        )
        conn.commit()
        conn.close()
        flash(f"Site {site_number} added.", "success")
    else:
        flash("Site number is required.", "danger")
    return redirect(url_for("sites"))

@app.route("/sites/<int:site_id>/toggle", methods=["POST"])
def toggle_site(site_id):
    db_path = current_app.config["DB_PATH"]
    conn = get_db(db_path)
    conn.execute("UPDATE sites SET active = 1 - active WHERE id=?", (site_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("sites"))

@app.route("/sites/<int:site_id>/remove", methods=["POST"])
def remove_site(site_id):
    db_path = current_app.config["DB_PATH"]
    conn = get_db(db_path)
    conn.execute("DELETE FROM sites WHERE id=?", (site_id,))
    conn.commit()
    conn.close()
    flash("Site removed.", "success")
    return redirect(url_for("sites"))
```

**Step 4: Update `web/templates/sites.html`**

```html
{% extends "base.html" %}
{% block title %}Sites{% endblock %}
{% block content %}
<h1 class="mb-4">Monitored Sites</h1>

<h4>Add Site by USGS Number</h4>
<form method="post" action="/sites/add" class="row g-2 mb-4">
  <div class="col-md-3"><input name="site_number" class="form-control" placeholder="USGS Site # (e.g. 03277200)" required></div>
  <div class="col-md-4"><input name="station_name" class="form-control" placeholder="Station name (optional)"></div>
  <div class="col-md-2">
    <select name="parameter_code" class="form-select">
      <option value="00060">Discharge (cfs)</option>
      <option value="00065">Gage height (ft)</option>
    </select>
  </div>
  <div class="col-md-2"><button class="btn btn-primary w-100">Add</button></div>
</form>

{% if sites %}
<table class="table table-striped">
  <thead><tr><th>Site #</th><th>Station Name</th><th>Parameter</th><th>Status</th><th>Actions</th></tr></thead>
  <tbody>
  {% for s in sites %}
  <tr>
    <td><code>{{ s.site_number }}</code></td>
    <td>{{ s.station_name or "—" }}</td>
    <td>{{ "Discharge" if s.parameter_code == "00060" else "Gage height" }}</td>
    <td>
      <form method="post" action="/sites/{{ s.id }}/toggle" style="display:inline">
        <button class="btn btn-sm {{ 'btn-success' if s.active else 'btn-outline-secondary' }}">
          {{ "Active" if s.active else "Inactive" }}
        </button>
      </form>
    </td>
    <td>
      <form method="post" action="/sites/{{ s.id }}/remove" style="display:inline">
        <button class="btn btn-sm btn-outline-danger">Remove</button>
      </form>
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="text-muted">No sites configured yet.</p>
{% endif %}
{% endblock %}
```

**Step 5: Run tests to verify they pass**

```bash
pytest tests/web/test_sites.py -v
```

Expected: 3 tests PASS.

**Step 6: Commit**

```bash
git add web/routes.py web/templates/sites.html tests/web/test_sites.py
git commit -m "feat: sites page with add/toggle/remove"
```

---

### Task 14: Settings Page

**Files:**
- Modify: `web/routes.py`
- Modify: `web/templates/settings.html`
- Create: `tests/web/test_settings.py`

**Step 1: Write failing tests**

Create `tests/web/test_settings.py`:

```python
import pytest
from db.models import init_db, get_db, get_setting
from web.app import create_app

@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_settings_page_shows_current_values(client, tmp_db):
    response = client.get("/settings")
    assert b"poll_interval" in response.data or b"Poll Interval" in response.data

def test_settings_post_updates_value(client, tmp_db):
    response = client.post("/settings", data={
        "poll_interval_minutes": "30",
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
    }, follow_redirects=True)
    assert response.status_code == 200
    assert get_setting("poll_interval_minutes", tmp_db) == "30"
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/web/test_settings.py -v
```

Expected: FAIL.

**Step 3: Add settings routes to `web/routes.py`**

```python
from db.models import get_db, get_setting, set_setting, DEFAULT_SETTINGS

SETTINGS_FIELDS = [
    ("poll_interval_minutes", "Poll Interval (minutes)", "number"),
    ("low_percentile", "Low Flow Percentile", "number"),
    ("high_percentile", "High Flow Percentile", "number"),
    ("very_low_percentile", "Very Low Percentile", "number"),
    ("very_high_percentile", "Very High Percentile", "number"),
    ("reminder_low_high_hours", "Reminder Interval: LOW/HIGH (hours)", "number"),
    ("reminder_severe_hours", "Reminder Interval: SEVERE (hours)", "number"),
    ("historical_start_year", "Historical Start Year", "number"),
    ("search_radius_miles", "Search Radius (miles)", "number"),
    ("telegram_bot_token", "Telegram Bot Token", "password"),
    ("twilio_account_sid", "Twilio Account SID", "text"),
    ("twilio_auth_token", "Twilio Auth Token", "password"),
    ("twilio_sms_number", "Twilio SMS Number", "text"),
    ("twilio_whatsapp_number", "Twilio WhatsApp Number", "text"),
    ("facebook_page_token", "Facebook Page Token", "password"),
    ("facebook_verify_token", "Facebook Verify Token", "text"),
]

@app.route("/settings", methods=["GET", "POST"])
def settings():
    db_path = current_app.config["DB_PATH"]
    if request.method == "POST":
        for key, _, _ in SETTINGS_FIELDS:
            value = request.form.get(key, "")
            set_setting(key, value, db_path)
        flash("Settings saved.", "success")
        return redirect(url_for("settings"))

    current = {key: get_setting(key, db_path, default="") for key, _, _ in SETTINGS_FIELDS}
    return render_template("settings.html", fields=SETTINGS_FIELDS, current=current)
```

**Step 4: Update `web/templates/settings.html`**

```html
{% extends "base.html" %}
{% block title %}Settings{% endblock %}
{% block content %}
<h1 class="mb-4">Settings</h1>
<form method="post" action="/settings">
  {% for key, label, input_type in fields %}
  <div class="mb-3 row">
    <label class="col-sm-4 col-form-label">{{ label }}</label>
    <div class="col-sm-6">
      <input type="{{ input_type }}" name="{{ key }}"
             value="{{ current[key] }}"
             class="form-control"
             {% if input_type == "password" %}autocomplete="new-password"{% endif %}>
    </div>
  </div>
  {% endfor %}
  <button type="submit" class="btn btn-primary">Save Settings</button>
</form>
{% endblock %}
```

**Step 5: Run tests to verify they pass**

```bash
pytest tests/web/test_settings.py -v
```

Expected: 2 tests PASS.

**Step 6: Commit**

```bash
git add web/routes.py web/templates/settings.html tests/web/test_settings.py
git commit -m "feat: settings page for all config including channel credentials"
```

---

### Task 15: Broadcast Page

**Files:**
- Modify: `web/routes.py`
- Modify: `web/templates/broadcast.html`
- Create: `tests/web/test_broadcast.py`

**Step 1: Write failing tests**

Create `tests/web/test_broadcast.py`:

```python
import pytest
from unittest.mock import patch
from db.models import init_db, get_db
from web.app import create_app
import queue

@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    q = queue.Queue()
    app = create_app(db_path=tmp_db, notification_queue=q)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c, q

def test_broadcast_get_returns_200(client):
    c, _ = client
    response = c.get("/broadcast")
    assert response.status_code == 200

def test_broadcast_post_puts_item_on_queue(client, tmp_db):
    c, q = client
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO subscribers (channel, channel_id, active) VALUES ('telegram', 'chat1', 1)")
    conn.commit()
    conn.close()

    response = c.post("/broadcast", data={
        "message": "Test broadcast message",
        "channels": ["telegram"],
    }, follow_redirects=True)
    assert response.status_code == 200
    assert not q.empty()
    item = q.get_nowait()
    assert item["type"] == "broadcast"
    assert "Test broadcast message" in item["data"]["message"]
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/web/test_broadcast.py -v
```

Expected: FAIL.

**Step 3: Add broadcast route to `web/routes.py`**

```python
@app.route("/broadcast", methods=["GET", "POST"])
def broadcast():
    db_path = current_app.config["DB_PATH"]
    notification_queue = current_app.config.get("NOTIFICATION_QUEUE")

    if request.method == "POST":
        message = request.form.get("message", "").strip()
        channels = request.form.getlist("channels")
        if message and notification_queue is not None:
            notification_queue.put({
                "type": "broadcast",
                "data": {
                    "message": message,
                    "channels": channels,
                }
            })
            flash("Broadcast queued.", "success")
        elif not message:
            flash("Message cannot be empty.", "danger")
        return redirect(url_for("broadcast"))

    return render_template("broadcast.html")
```

Also update `monitor/dispatcher.py` `run_once` to handle `broadcast` type:

In the `if item["type"] == "transition":` block, add:

```python
elif item["type"] == "broadcast":
    message = item["data"]["message"]
    allowed_channels = set(item["data"].get("channels", list(self.adapters.keys())))
    trigger_type = "manual"
    site_id = None
    subscribers = [s for s in get_active_subscribers(self.db_path) if s["channel"] in allowed_channels]
    for sub in subscribers:
        adapter = self.adapters.get(sub["channel"])
        if adapter is None:
            continue
        try:
            success = adapter.send(sub["channel_id"], message)
            log_notification(sub["id"], None, sub["channel"], message, trigger_type, success, db_path=self.db_path)
        except Exception as e:
            logger.exception("Failed broadcast to %s/%s", sub["channel"], sub["channel_id"])
    return  # skip the normal subscriber loop below
```

**Step 4: Update `web/templates/broadcast.html`**

```html
{% extends "base.html" %}
{% block title %}Broadcast{% endblock %}
{% block content %}
<h1 class="mb-4">Send Broadcast</h1>
<form method="post" action="/broadcast">
  <div class="mb-3">
    <label class="form-label">Message</label>
    <textarea name="message" class="form-control" rows="4" required></textarea>
  </div>
  <div class="mb-3">
    <label class="form-label">Send to channels</label><br>
    {% for ch in ["telegram", "sms", "whatsapp", "facebook"] %}
    <div class="form-check form-check-inline">
      <input class="form-check-input" type="checkbox" name="channels" value="{{ ch }}" id="ch_{{ ch }}" checked>
      <label class="form-check-label" for="ch_{{ ch }}">{{ ch|capitalize }}</label>
    </div>
    {% endfor %}
  </div>
  <button type="submit" class="btn btn-warning">Send Broadcast</button>
</form>
{% endblock %}
```

**Step 5: Run tests to verify they pass**

```bash
pytest tests/web/test_broadcast.py -v
```

Expected: 2 tests PASS.

**Step 6: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

**Step 7: Commit**

```bash
git add web/routes.py web/templates/broadcast.html monitor/dispatcher.py tests/web/test_broadcast.py
git commit -m "feat: broadcast page with manual message dispatch to all channels"
```

---

## Phase 5: Windows Service

### Task 16: pywin32 Service Entry Point

**Files:**
- Create: `service.py`

> pywin32 services cannot be unit tested without a Windows service manager. The test procedure below is manual.

**Step 1: Create `service.py`**

```python
"""
River Monitor Windows Service
Install:   python service.py install
Start:     python service.py start
Stop:      python service.py stop
Remove:    python service.py remove
Debug run: python service.py debug
"""

import os
import sys
import queue
import threading
import logging
import logging.handlers
import importlib.util

# Ensure project root is on path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DB_PATH = os.path.join(BASE_DIR, "db", "river_monitor.db")
LOG_PATH = os.path.join(BASE_DIR, "logs", "river_monitor.log")

try:
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False


def setup_logging():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root.addHandler(handler)


def migrate_legacy_config(db_path):
    """Import config.py and seed the database if it exists and has sites."""
    config_path = os.path.join(BASE_DIR, "config.py")
    if not os.path.exists(config_path):
        return
    spec = importlib.util.spec_from_file_location("config", config_path)
    config_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_module)
    from db.migration import migrate_from_config
    migrate_from_config(config_module, db_path)


def build_adapters(db_path):
    adapters = []
    try:
        from monitor.adapters.telegram import TelegramAdapter
        t = TelegramAdapter(db_path=db_path)
        adapters.append(t)
    except Exception as e:
        logging.warning("Telegram adapter unavailable: %s", e)
    try:
        from monitor.adapters.sms import SMSAdapter
        adapters.append(SMSAdapter(db_path=db_path))
    except Exception as e:
        logging.warning("SMS adapter unavailable: %s", e)
    try:
        from monitor.adapters.whatsapp import WhatsAppAdapter
        adapters.append(WhatsAppAdapter(db_path=db_path))
    except Exception as e:
        logging.warning("WhatsApp adapter unavailable: %s", e)
    try:
        from monitor.adapters.facebook import FacebookAdapter
        adapters.append(FacebookAdapter(db_path=db_path))
    except Exception as e:
        logging.warning("Facebook adapter unavailable: %s", e)
    return adapters


def run_service(db_path=DB_PATH, stop_event=None):
    """Start all threads. Blocks until stop_event is set."""
    setup_logging()
    logger = logging.getLogger("service")
    logger.info("River Monitor service starting (db: %s)", db_path)

    from db.models import init_db
    init_db(db_path)
    migrate_legacy_config(db_path)

    stop_event = stop_event or threading.Event()
    notif_queue = queue.Queue()

    adapters = build_adapters(db_path)

    from monitor.polling import PollingThread
    from monitor.scheduler import SchedulerThread
    from monitor.dispatcher import NotificationDispatcher
    from web.app import create_app
    from threading import Thread

    polling = PollingThread(notif_queue, db_path=db_path, stop_event=stop_event)
    scheduler = SchedulerThread(notif_queue, db_path=db_path, stop_event=stop_event)
    dispatcher = NotificationDispatcher(notif_queue, adapters=[a for a in adapters if not isinstance(a, __import__('threading').Thread)], db_path=db_path, stop_event=stop_event)

    # Start Telegram (it's a Thread subclass)
    telegram_threads = [a for a in adapters if isinstance(a, __import__('threading').Thread)]
    for t in telegram_threads:
        t.start()

    # Flask web thread
    flask_app = create_app(db_path=db_path, notification_queue=notif_queue)

    def run_flask():
        flask_app.run(host="127.0.0.1", port=8080, use_reloader=False, threaded=True)

    web_thread = Thread(target=run_flask, name="WebThread", daemon=True)

    for thread in [polling, scheduler, dispatcher, web_thread]:
        thread.start()

    logger.info("All threads started. Portal at http://localhost:8080")
    stop_event.wait()
    logger.info("Stop event received — shutting down")


if WIN32_AVAILABLE:
    class RiverMonitorService(win32serviceutil.ServiceFramework):
        _svc_name_ = "RiverMonitor"
        _svc_display_name_ = "River Level Monitor Service"
        _svc_description_ = "Monitors USGS stream gauges and sends condition alerts."

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.stop_event = threading.Event()
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.stop_event.set()
            win32event.SetEvent(self.hWaitStop)

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, "")
            )
            run_service(stop_event=self.stop_event)


if __name__ == "__main__":
    if "--debug" in sys.argv or "debug" in sys.argv:
        # Allow running outside the service manager for testing
        print("Running in debug mode on http://localhost:8080")
        run_service()
    elif WIN32_AVAILABLE:
        win32serviceutil.HandleCommandLine(RiverMonitorService)
    else:
        print("pywin32 not available. Run: pip install pywin32")
        print("Or use debug mode: python service.py debug")
```

**Step 2: Manual test — debug mode (no service manager needed)**

```bash
python service.py debug
```

Expected:
- No import errors
- "All threads started" in console
- `http://localhost:8080` opens in browser and shows dashboard

**Step 3: Manual test — install as Windows service**

Run in an elevated (Administrator) command prompt:

```bash
python service.py install
python service.py start
```

Expected: service appears in `services.msc` as "River Level Monitor Service", status Running.

Check log:

```bash
type logs\river_monitor.log
```

Expected: startup messages, "All threads started."

**Step 4: Stop and verify graceful shutdown**

```bash
python service.py stop
```

Expected: service stops cleanly; log shows "Stop event received — shutting down".

**Step 5: Commit**

```bash
git add service.py
git commit -m "feat: pywin32 windows service entry point with all thread orchestration"
```

---

## Final Validation

**Step 1: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests pass, 0 failures.

**Step 2: Update CLAUDE.md**

Add service commands to the Commands section of `CLAUDE.md`:

```markdown
## Service Commands (run as Administrator)
python service.py install   # register Windows service
python service.py start
python service.py stop
python service.py remove
python service.py debug     # run without service manager (for development)
```

**Step 3: Final commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with service commands"
```
