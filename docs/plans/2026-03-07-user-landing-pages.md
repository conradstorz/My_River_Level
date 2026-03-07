# User Landing Pages Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add per-user shareable landing pages that embed NOAA hydrograph images and live condition badges, backed by a new NOAA gauge polling loop that sends notifications to per-page subscribers on severity changes.

**Architecture:** Four new DB tables (`user_pages`, `noaa_gauges`, `page_noaa_gauges`, `page_subscribers`) are added to the existing SQLite schema. A new `NoaaPollingThread` runs alongside `PollingThread`, polling the NOAA NWPS API and enqueuing `noaa_transition` events. The dispatcher is extended to handle that new event type and route messages to page-scoped subscribers. New Flask routes and templates provide page creation, public view, edit, subscribe, and admin moderation.

**Tech Stack:** Python 3, SQLite, Flask/Jinja2, Bootstrap 5, `requests` (already available via `dataretrieval`), NOAA NWPS REST API (`api.water.noaa.gov/nwps/v1`).

---

### Task 1: DB Schema — Four New Tables

**Files:**
- Modify: `db/models.py`
- Test: `tests/db/test_models.py`

**Step 1: Write the failing tests**

Add to `tests/db/test_models.py`:

```python
def test_new_tables_created(tmp_db):
    from db.models import init_db, get_db
    init_db(tmp_db)
    conn = get_db(tmp_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "user_pages" in tables
    assert "noaa_gauges" in tables
    assert "page_noaa_gauges" in tables
    assert "page_subscribers" in tables
```

**Step 2: Run to verify it fails**

```
pytest tests/db/test_models.py::test_new_tables_created -v
```
Expected: FAIL — tables not found.

**Step 3: Add tables to the SCHEMA string in `db/models.py`**

Append to the `SCHEMA` string (after the closing `"""` of existing tables, before the final `"""`):

```sql
CREATE TABLE IF NOT EXISTS user_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_token TEXT NOT NULL UNIQUE,
    edit_token TEXT NOT NULL UNIQUE,
    page_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS noaa_gauges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lid TEXT NOT NULL UNIQUE,
    station_name TEXT NOT NULL DEFAULT '',
    current_stage REAL,
    action_stage REAL,
    minor_flood_stage REAL,
    moderate_flood_stage REAL,
    major_flood_stage REAL,
    severity TEXT NOT NULL DEFAULT 'Unknown',
    last_polled_at TEXT
);

CREATE TABLE IF NOT EXISTS page_noaa_gauges (
    page_id INTEGER NOT NULL REFERENCES user_pages(id),
    noaa_gauge_id INTEGER NOT NULL REFERENCES noaa_gauges(id),
    PRIMARY KEY (page_id, noaa_gauge_id)
);

CREATE TABLE IF NOT EXISTS page_subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES user_pages(id),
    channel TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    opted_in_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(page_id, channel, channel_id)
);
```

**Step 4: Run to verify it passes**

```
pytest tests/db/test_models.py::test_new_tables_created -v
```
Expected: PASS.

**Step 5: Commit**

```bash
git add db/models.py tests/db/test_models.py
git commit -m "feat: db schema for user landing pages and noaa gauges"
```

---

### Task 2: DB Helper Functions

**Files:**
- Modify: `db/models.py`
- Test: `tests/db/test_models.py`

**Step 1: Write the failing tests**

Add to `tests/db/test_models.py`:

```python
def test_create_user_page(tmp_db):
    from db.models import init_db, create_user_page, get_page_by_public_token, get_page_by_edit_token
    init_db(tmp_db)
    pub, edit = create_user_page("My Page", tmp_db)
    assert len(pub) == 36   # UUID format
    assert len(edit) == 36
    assert pub != edit
    page = get_page_by_public_token(pub, tmp_db)
    assert page["page_name"] == "My Page"
    assert page["active"] == 1
    page2 = get_page_by_edit_token(edit, tmp_db)
    assert page2["id"] == page["id"]


def test_noaa_gauge_helpers(tmp_db):
    from db.models import init_db, get_or_create_noaa_gauge, get_all_noaa_gauges, update_noaa_gauge_condition
    init_db(tmp_db)
    gid = get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, tmp_db)
    assert isinstance(gid, int)
    # Calling again returns same id
    gid2 = get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, tmp_db)
    assert gid == gid2
    update_noaa_gauge_condition("MLUK2", 17.5, "Normal", tmp_db)
    gauges = get_all_noaa_gauges(tmp_db)
    assert len(gauges) == 1
    assert gauges[0]["severity"] == "Normal"


def test_page_gauge_link(tmp_db):
    from db.models import init_db, create_user_page, get_or_create_noaa_gauge, link_page_gauge, unlink_page_gauge, get_page_gauges
    init_db(tmp_db)
    pub, edit = create_user_page("Test", tmp_db)
    from db.models import get_page_by_public_token
    page = get_page_by_public_token(pub, tmp_db)
    gid = get_or_create_noaa_gauge("MLUK2", "Ohio River", 21.0, 23.0, 30.0, 38.0, tmp_db)
    link_page_gauge(page["id"], gid, tmp_db)
    gauges = get_page_gauges(page["id"], tmp_db)
    assert len(gauges) == 1
    assert gauges[0]["lid"] == "MLUK2"
    unlink_page_gauge(page["id"], gid, tmp_db)
    assert get_page_gauges(page["id"], tmp_db) == []


def test_page_subscriber_helpers(tmp_db):
    from db.models import init_db, create_user_page, get_page_by_public_token, add_page_subscriber, set_page_subscriber_status, get_active_page_subscribers
    init_db(tmp_db)
    pub, _ = create_user_page("Test", tmp_db)
    from db.models import get_page_by_public_token
    page = get_page_by_public_token(pub, tmp_db)
    add_page_subscriber(page["id"], "sms", "+15025551234", "Alice", tmp_db)
    subs = get_active_page_subscribers(page["id"], tmp_db)
    assert len(subs) == 1
    assert subs[0]["channel_id"] == "+15025551234"
    set_page_subscriber_status(page["id"], "sms", "+15025551234", "paused", tmp_db)
    assert get_active_page_subscribers(page["id"], tmp_db) == []


def test_get_pages_for_noaa_gauge(tmp_db):
    from db.models import (init_db, create_user_page, get_page_by_public_token,
                           get_or_create_noaa_gauge, link_page_gauge,
                           get_pages_for_noaa_gauge)
    init_db(tmp_db)
    pub1, _ = create_user_page("Page 1", tmp_db)
    pub2, _ = create_user_page("Page 2", tmp_db)
    p1 = get_page_by_public_token(pub1, tmp_db)
    p2 = get_page_by_public_token(pub2, tmp_db)
    gid = get_or_create_noaa_gauge("MLUK2", "Ohio River", 21.0, 23.0, 30.0, 38.0, tmp_db)
    link_page_gauge(p1["id"], gid, tmp_db)
    link_page_gauge(p2["id"], gid, tmp_db)
    pages = get_pages_for_noaa_gauge(gid, tmp_db)
    assert len(pages) == 2
```

**Step 2: Run to verify they fail**

```
pytest tests/db/test_models.py -k "test_create_user_page or test_noaa_gauge or test_page_gauge or test_page_subscriber or test_get_pages" -v
```
Expected: FAIL — functions not defined.

**Step 3: Add helper functions to `db/models.py`**

Add after the existing helper functions at the bottom of the file:

```python
import uuid


def create_user_page(page_name, db_path=None):
    """Create a new user page. Returns (public_token, edit_token)."""
    public_token = str(uuid.uuid4())
    edit_token = str(uuid.uuid4())
    conn = get_db(db_path)
    conn.execute(
        "INSERT INTO user_pages (public_token, edit_token, page_name) VALUES (?, ?, ?)",
        (public_token, edit_token, page_name)
    )
    conn.commit()
    conn.close()
    return public_token, edit_token


def get_page_by_public_token(token, db_path=None):
    conn = get_db(db_path)
    row = conn.execute("SELECT * FROM user_pages WHERE public_token=?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_page_by_edit_token(token, db_path=None):
    conn = get_db(db_path)
    row = conn.execute("SELECT * FROM user_pages WHERE edit_token=?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_or_create_noaa_gauge(lid, station_name, action_stage, minor_stage,
                              moderate_stage, major_stage, db_path=None):
    """Insert gauge if not present; return its id."""
    conn = get_db(db_path)
    conn.execute(
        """INSERT OR IGNORE INTO noaa_gauges
           (lid, station_name, action_stage, minor_flood_stage, moderate_flood_stage, major_flood_stage)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (lid, station_name, action_stage, minor_stage, moderate_stage, major_stage)
    )
    conn.commit()
    row = conn.execute("SELECT id FROM noaa_gauges WHERE lid=?", (lid,)).fetchone()
    conn.close()
    return row["id"]


def update_noaa_gauge_condition(lid, current_stage, severity, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        """UPDATE noaa_gauges SET current_stage=?, severity=?, last_polled_at=datetime('now')
           WHERE lid=?""",
        (current_stage, severity, lid)
    )
    conn.commit()
    conn.close()


def get_all_noaa_gauges(db_path=None):
    conn = get_db(db_path)
    rows = conn.execute("SELECT * FROM noaa_gauges").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def link_page_gauge(page_id, gauge_id, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO page_noaa_gauges (page_id, noaa_gauge_id) VALUES (?, ?)",
        (page_id, gauge_id)
    )
    conn.commit()
    conn.close()


def unlink_page_gauge(page_id, gauge_id, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        "DELETE FROM page_noaa_gauges WHERE page_id=? AND noaa_gauge_id=?",
        (page_id, gauge_id)
    )
    conn.commit()
    conn.close()


def get_page_gauges(page_id, db_path=None):
    """Return all noaa_gauges linked to a page."""
    conn = get_db(db_path)
    rows = conn.execute(
        """SELECT ng.* FROM noaa_gauges ng
           JOIN page_noaa_gauges png ON png.noaa_gauge_id = ng.id
           WHERE png.page_id=?""",
        (page_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pages_for_noaa_gauge(gauge_id, db_path=None):
    """Return all active pages that include this gauge."""
    conn = get_db(db_path)
    rows = conn.execute(
        """SELECT up.* FROM user_pages up
           JOIN page_noaa_gauges png ON png.page_id = up.id
           WHERE png.noaa_gauge_id=? AND up.active=1""",
        (gauge_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_page_subscriber(page_id, channel, channel_id, display_name, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO page_subscribers
           (page_id, channel, channel_id, display_name, status)
           VALUES (?, ?, ?, ?, 'active')""",
        (page_id, channel, channel_id, display_name)
    )
    conn.commit()
    conn.close()


def set_page_subscriber_status(page_id, channel, channel_id, status, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        """UPDATE page_subscribers SET status=?
           WHERE page_id=? AND channel=? AND channel_id=?""",
        (status, page_id, channel, channel_id)
    )
    conn.commit()
    conn.close()


def get_active_page_subscribers(page_id, db_path=None):
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT * FROM page_subscribers WHERE page_id=? AND status='active'",
        (page_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_page_subscribers_for_gauge(gauge_id, db_path=None):
    """Return all active page_subscribers for every page linked to this gauge."""
    conn = get_db(db_path)
    rows = conn.execute(
        """SELECT ps.* FROM page_subscribers ps
           JOIN page_noaa_gauges png ON png.page_id = ps.page_id
           WHERE png.noaa_gauge_id=? AND ps.status='active'""",
        (gauge_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

**Step 4: Run to verify they pass**

```
pytest tests/db/test_models.py -v
```
Expected: all PASS.

**Step 5: Commit**

```bash
git add db/models.py tests/db/test_models.py
git commit -m "feat: db helpers for user pages, noaa gauges, and page subscribers"
```

---

### Task 3: NOAA API Client + Condition Classifier

**Files:**
- Create: `monitor/noaa_client.py`
- Create: `tests/monitor/test_noaa_client.py`

**Step 1: Write the failing tests**

Create `tests/monitor/test_noaa_client.py`:

```python
from unittest.mock import patch, MagicMock
from monitor.noaa_client import classify_noaa_condition, fetch_gauge_metadata, fetch_current_stage


# ── Condition classifier ────────────────────────────────────────────────────

def test_classify_normal():
    assert classify_noaa_condition(15.0, 21.0, 23.0, 30.0, 38.0) == "Normal"

def test_classify_action():
    assert classify_noaa_condition(21.5, 21.0, 23.0, 30.0, 38.0) == "Action"

def test_classify_minor():
    assert classify_noaa_condition(24.0, 21.0, 23.0, 30.0, 38.0) == "Minor"

def test_classify_moderate():
    assert classify_noaa_condition(31.0, 21.0, 23.0, 30.0, 38.0) == "Moderate"

def test_classify_major():
    assert classify_noaa_condition(40.0, 21.0, 23.0, 30.0, 38.0) == "Major"

def test_classify_none_stage():
    assert classify_noaa_condition(None, 21.0, 23.0, 30.0, 38.0) == "Unknown"

def test_classify_missing_thresholds():
    # Missing thresholds treated as no upper limit crossed
    assert classify_noaa_condition(50.0, 21.0, None, None, None) == "Action"


# ── API fetches ─────────────────────────────────────────────────────────────

def _mock_metadata_response():
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "lid": "MLUK2",
        "name": "Ohio River at McAlpine Upper",
        "flood": {
            "categories": [
                {"name": "action",   "stage": 21.0},
                {"name": "minor",    "stage": 23.0},
                {"name": "moderate", "stage": 30.0},
                {"name": "major",    "stage": 38.0},
            ]
        }
    }
    return mock


def test_fetch_gauge_metadata():
    with patch("monitor.noaa_client.requests.get", return_value=_mock_metadata_response()):
        meta = fetch_gauge_metadata("MLUK2")
    assert meta["station_name"] == "Ohio River at McAlpine Upper"
    assert meta["action_stage"] == 21.0
    assert meta["minor_flood_stage"] == 23.0
    assert meta["moderate_flood_stage"] == 30.0
    assert meta["major_flood_stage"] == 38.0


def test_fetch_gauge_metadata_http_error():
    mock = MagicMock()
    mock.status_code = 404
    with patch("monitor.noaa_client.requests.get", return_value=mock):
        meta = fetch_gauge_metadata("BADLID")
    assert meta is None


def test_fetch_current_stage():
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "data": [
            {"validTime": "2026-03-07T12:00:00Z", "primary": 17.16},
            {"validTime": "2026-03-07T12:05:00Z", "primary": 17.20},
        ]
    }
    with patch("monitor.noaa_client.requests.get", return_value=mock):
        stage = fetch_current_stage("MLUK2")
    assert stage == 17.20


def test_fetch_current_stage_empty_data():
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"data": []}
    with patch("monitor.noaa_client.requests.get", return_value=mock):
        stage = fetch_current_stage("MLUK2")
    assert stage is None
```

**Step 2: Run to verify they fail**

```
pytest tests/monitor/test_noaa_client.py -v
```
Expected: FAIL — module not found.

**Step 3: Create `monitor/noaa_client.py`**

```python
import logging
import requests

logger = logging.getLogger(__name__)

NWPS_BASE = "https://api.water.noaa.gov/nwps/v1"
TIMEOUT = 10


def classify_noaa_condition(stage, action_stage, minor_stage, moderate_stage, major_stage):
    """Map current stage to a severity label using NOAA flood category thresholds."""
    if stage is None:
        return "Unknown"
    if major_stage is not None and stage >= major_stage:
        return "Major"
    if moderate_stage is not None and stage >= moderate_stage:
        return "Moderate"
    if minor_stage is not None and stage >= minor_stage:
        return "Minor"
    if action_stage is not None and stage >= action_stage:
        return "Action"
    return "Normal"


def fetch_gauge_metadata(lid):
    """
    Fetch station name and flood thresholds from the NWPS gauge endpoint.

    NOTE: The exact JSON structure of the NWPS API response should be verified
    by inspecting a live response. This implementation handles the most likely
    structure; adjust field names if the API returns different keys.

    Returns a dict with keys:
        station_name, action_stage, minor_flood_stage,
        moderate_flood_stage, major_flood_stage
    or None on error.
    """
    url = f"{NWPS_BASE}/gauges/{lid.lower()}"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            logger.warning("NOAA metadata fetch failed for %s: HTTP %s", lid, resp.status_code)
            return None
        data = resp.json()
    except Exception:
        logger.exception("Error fetching NOAA metadata for %s", lid)
        return None

    # Parse flood categories — handle list or dict structures defensively
    thresholds = {"action_stage": None, "minor_flood_stage": None,
                  "moderate_flood_stage": None, "major_flood_stage": None}
    categories = (data.get("flood") or {}).get("categories", [])
    key_map = {
        "action":   "action_stage",
        "minor":    "minor_flood_stage",
        "moderate": "moderate_flood_stage",
        "major":    "major_flood_stage",
    }
    for cat in categories:
        name = (cat.get("name") or "").lower()
        if name in key_map:
            thresholds[key_map[name]] = cat.get("stage")

    return {
        "station_name": data.get("name", lid),
        **thresholds,
    }


def fetch_current_stage(lid):
    """
    Fetch the most recent observed stage from the NWPS stageflow endpoint.
    Returns a float (feet) or None.
    """
    url = f"{NWPS_BASE}/gauges/{lid.lower()}/stageflow/observed"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            logger.warning("NOAA stage fetch failed for %s: HTTP %s", lid, resp.status_code)
            return None
        data = resp.json()
        readings = data.get("data", [])
        if not readings:
            return None
        return readings[-1].get("primary")
    except Exception:
        logger.exception("Error fetching NOAA stage for %s", lid)
        return None
```

**Step 4: Run to verify they pass**

```
pytest tests/monitor/test_noaa_client.py -v
```
Expected: all PASS.

**Step 5: Commit**

```bash
git add monitor/noaa_client.py tests/monitor/test_noaa_client.py
git commit -m "feat: noaa api client and condition classifier"
```

---

### Task 4: NOAA Polling Thread

**Files:**
- Create: `monitor/noaa_polling.py`
- Create: `tests/monitor/test_noaa_polling.py`

**Step 1: Write the failing tests**

Create `tests/monitor/test_noaa_polling.py`:

```python
import queue
from unittest.mock import patch, MagicMock
from monitor.noaa_polling import fetch_and_evaluate_noaa_gauge, NoaaPollingThread


def _gauge(severity="Normal"):
    return {
        "id": 1, "lid": "MLUK2", "station_name": "Ohio River at McAlpine Upper",
        "action_stage": 21.0, "minor_flood_stage": 23.0,
        "moderate_flood_stage": 30.0, "major_flood_stage": 38.0,
        "severity": severity,
    }


def test_fetch_no_change(tmp_db):
    from db.models import init_db, get_or_create_noaa_gauge
    init_db(tmp_db)
    get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, tmp_db)
    with patch("monitor.noaa_polling.fetch_current_stage", return_value=15.0):
        result = fetch_and_evaluate_noaa_gauge(_gauge("Normal"), tmp_db)
    assert result is None   # No transition


def test_fetch_transition(tmp_db):
    from db.models import init_db, get_or_create_noaa_gauge
    init_db(tmp_db)
    get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, tmp_db)
    with patch("monitor.noaa_polling.fetch_current_stage", return_value=22.0):
        result = fetch_and_evaluate_noaa_gauge(_gauge("Normal"), tmp_db)
    assert result is not None
    assert result["previous_severity"] == "Normal"
    assert result["new_severity"] == "Action"
    assert result["current_stage"] == 22.0


def test_polling_thread_enqueues(tmp_db):
    from db.models import init_db, get_or_create_noaa_gauge
    init_db(tmp_db)
    get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, tmp_db)
    q = queue.Queue()
    thread = NoaaPollingThread(q, db_path=tmp_db)
    with patch("monitor.noaa_polling.fetch_current_stage", return_value=22.0):
        thread._poll()
    assert not q.empty()
    item = q.get()
    assert item["type"] == "noaa_transition"
```

**Step 2: Run to verify they fail**

```
pytest tests/monitor/test_noaa_polling.py -v
```
Expected: FAIL — module not found.

**Step 3: Create `monitor/noaa_polling.py`**

```python
import threading
import logging

from db.models import get_db, get_setting, get_all_noaa_gauges, update_noaa_gauge_condition
from monitor.noaa_client import fetch_current_stage, classify_noaa_condition

logger = logging.getLogger(__name__)


def fetch_and_evaluate_noaa_gauge(gauge, db_path=None):
    """
    Fetch current stage for one NOAA gauge, classify condition, update DB.
    Returns a transition dict if severity changed, else None.
    """
    lid = gauge["lid"]
    stage = fetch_current_stage(lid)
    if stage is None:
        return None

    new_severity = classify_noaa_condition(
        stage,
        gauge["action_stage"],
        gauge["minor_flood_stage"],
        gauge["moderate_flood_stage"],
        gauge["major_flood_stage"],
    )
    previous_severity = gauge["severity"]
    update_noaa_gauge_condition(lid, stage, new_severity, db_path)

    if new_severity != previous_severity:
        return {
            "gauge_id": gauge["id"],
            "lid": lid,
            "station_name": gauge["station_name"],
            "previous_severity": previous_severity,
            "new_severity": new_severity,
            "current_stage": stage,
        }
    return None


class NoaaPollingThread(threading.Thread):
    def __init__(self, notification_queue, db_path=None, stop_event=None):
        super().__init__(name="NoaaPollingThread", daemon=True)
        self.notification_queue = notification_queue
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()

    def run(self):
        logger.info("NoaaPollingThread started")
        while not self.stop_event.is_set():
            self._poll()
            interval = int(get_setting("poll_interval_minutes", self.db_path, default="15"))
            self.stop_event.wait(timeout=interval * 60)
        logger.info("NoaaPollingThread stopped")

    def _poll(self):
        gauges = get_all_noaa_gauges(self.db_path)
        for gauge in gauges:
            try:
                transition = fetch_and_evaluate_noaa_gauge(gauge, self.db_path)
                if transition:
                    self.notification_queue.put({
                        "type": "noaa_transition",
                        "data": transition,
                    })
            except Exception:
                logger.exception("Error polling NOAA gauge %s", gauge["lid"])
```

**Step 4: Run to verify they pass**

```
pytest tests/monitor/test_noaa_polling.py -v
```
Expected: all PASS.

**Step 5: Commit**

```bash
git add monitor/noaa_polling.py tests/monitor/test_noaa_polling.py
git commit -m "feat: noaa polling thread for gauge condition monitoring"
```

---

### Task 5: Dispatcher Handles `noaa_transition`

**Files:**
- Modify: `monitor/dispatcher.py`
- Test: `tests/monitor/test_dispatcher.py`

**Step 1: Write the failing test**

Add to `tests/monitor/test_dispatcher.py`:

```python
def test_noaa_transition_dispatched(tmp_db):
    import queue
    from unittest.mock import MagicMock, patch
    from db.models import init_db, create_user_page, get_page_by_public_token, get_or_create_noaa_gauge, link_page_gauge, add_page_subscriber
    from monitor.dispatcher import NotificationDispatcher

    init_db(tmp_db)
    pub, _ = create_user_page("Test Page", tmp_db)
    page = get_page_by_public_token(pub, tmp_db)
    gid = get_or_create_noaa_gauge("MLUK2", "Ohio River", 21.0, 23.0, 30.0, 38.0, tmp_db)
    link_page_gauge(page["id"], gid, tmp_db)
    add_page_subscriber(page["id"], "sms", "+15025551234", "Alice", tmp_db)

    mock_adapter = MagicMock()
    mock_adapter.channel = "sms"
    mock_adapter.send.return_value = True

    q = queue.Queue()
    q.put({
        "type": "noaa_transition",
        "data": {
            "gauge_id": gid,
            "lid": "MLUK2",
            "station_name": "Ohio River at McAlpine Upper",
            "previous_severity": "Normal",
            "new_severity": "Action",
            "current_stage": 21.5,
        }
    })
    q.put(None)  # sentinel to stop dispatcher

    dispatcher = NotificationDispatcher(q, adapters=[mock_adapter], db_path=tmp_db)
    dispatcher.run_once()  # process noaa_transition
    mock_adapter.send.assert_called_once()
    call_args = mock_adapter.send.call_args
    assert "+15025551234" in call_args[0]
    assert "MLUK2" in call_args[0][1] or "McAlpine" in call_args[0][1]
```

**Step 2: Run to verify it fails**

```
pytest tests/monitor/test_dispatcher.py::test_noaa_transition_dispatched -v
```
Expected: FAIL.

**Step 3: Extend `monitor/dispatcher.py`**

Add a new format function and extend `run_once()`. Add after `format_reminder_message`:

```python
def format_noaa_transition_message(data):
    return (
        f"⚠️ River Level Change: {data['station_name']} ({data['lid']})\n"
        f"Condition changed: {data['previous_severity']} → {data['new_severity']}\n"
        f"Current stage: {data['current_stage']:.2f} ft\n"
        f"View: https://water.noaa.gov/gauges/{data['lid'].lower()}"
    )
```

Add a new import at the top of `monitor/dispatcher.py`:
```python
from db.models import get_db, get_page_subscribers_for_gauge
```
(replace the existing `from db.models import get_db` import)

In `run_once()`, add a new branch in the `try` block after the existing `elif item["type"] == "reminder":` block:

```python
            elif item["type"] == "noaa_transition":
                message = format_noaa_transition_message(item["data"])
                trigger_type = "noaa_transition"
                gauge_id = item["data"]["gauge_id"]
                subscribers = get_page_subscribers_for_gauge(gauge_id, self.db_path)
                for sub in subscribers:
                    adapter = self.adapters.get(sub["channel"])
                    if adapter is None:
                        continue
                    try:
                        success = adapter.send(sub["channel_id"], message)
                        log_notification(sub["id"], None, sub["channel"],
                                         message, trigger_type, success, db_path=self.db_path)
                    except Exception as e:
                        logger.exception("Failed noaa notify to %s/%s", sub["channel"], sub["channel_id"])
                        log_notification(sub["id"], None, sub["channel"],
                                         message, trigger_type, False, str(e), db_path=self.db_path)
                return
```

**Step 4: Run to verify it passes**

```
pytest tests/monitor/test_dispatcher.py -v
```
Expected: all PASS.

**Step 5: Commit**

```bash
git add monitor/dispatcher.py tests/monitor/test_dispatcher.py
git commit -m "feat: dispatcher handles noaa_transition events"
```

---

### Task 6: Wire `NoaaPollingThread` into the Service

**Files:**
- Modify: `service.py`

**Step 1: In `service.py`, inside `run_service()`, add after the existing `polling = PollingThread(...)` line:**

```python
    from monitor.noaa_polling import NoaaPollingThread
    noaa_polling = NoaaPollingThread(notif_queue, db_path=db_path, stop_event=stop_event)
```

**Step 2: Start the thread alongside the others** — find the block where `polling.start()` is called and add:

```python
    noaa_polling.start()
```

**Step 3: Verify the service still starts cleanly in debug mode**

```
python service.py debug
```
Expected: starts without error, logs show "NoaaPollingThread started".
Press Ctrl+C to stop.

**Step 4: Commit**

```bash
git add service.py
git commit -m "feat: start noaa polling thread in service"
```

---

### Task 7: Web Routes — Page Creation

**Files:**
- Modify: `web/routes.py`
- Create: `web/templates/page_new.html`
- Create: `web/templates/page_created.html`
- Test: `tests/web/test_pages.py`

**Step 1: Write the failing tests**

Create `tests/web/test_pages.py`:

```python
import pytest
from db.models import init_db


@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    from web.app import create_app
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_new_page_get(client):
    resp = client.get("/pages/new")
    assert resp.status_code == 200
    assert b"Create" in resp.data


def test_new_page_post_creates_page(client):
    resp = client.post("/pages/new", data={"page_name": "My River Page"}, follow_redirects=False)
    # Should redirect to a confirmation page or render with tokens
    assert resp.status_code in (200, 302)


def test_new_page_post_shows_tokens(client):
    resp = client.post("/pages/new", data={"page_name": "Test Page"}, follow_redirects=True)
    assert resp.status_code == 200
    # Both token URLs should appear in the response
    assert b"/view/" in resp.data
    assert b"/edit/" in resp.data


def test_new_page_missing_name(client):
    resp = client.post("/pages/new", data={"page_name": ""}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"required" in resp.data.lower() or b"name" in resp.data.lower()
```

**Step 2: Run to verify they fail**

```
pytest tests/web/test_pages.py -v
```
Expected: FAIL — routes not found.

**Step 3: Add routes to `web/routes.py`**

Add inside `register_routes(app)`:

```python
    @app.route("/pages/new", methods=["GET", "POST"])
    def page_new():
        if request.method == "POST":
            page_name = request.form.get("page_name", "").strip()
            if not page_name:
                flash("Page name is required.", "danger")
                return render_template("page_new.html")
            from db.models import create_user_page
            db_path = current_app.config["DB_PATH"]
            public_token, edit_token = create_user_page(page_name, db_path)
            return render_template("page_created.html",
                                   page_name=page_name,
                                   public_token=public_token,
                                   edit_token=edit_token)
        return render_template("page_new.html")
```

**Step 4: Create `web/templates/page_new.html`**

```html
{% extends "base.html" %}
{% block title %}Create a Page{% endblock %}
{% block content %}
<div class="row justify-content-center">
  <div class="col-md-6">
    <h1 class="mb-4">Create Your River Page</h1>
    <p class="text-muted">Choose a name for your page. You'll get a shareable link and a private edit link.</p>
    <form method="post">
      <div class="mb-3">
        <label class="form-label">Page Name</label>
        <input type="text" name="page_name" class="form-control" placeholder="e.g. My Ohio River Gauges" required>
      </div>
      <button type="submit" class="btn btn-primary">Create Page</button>
    </form>
  </div>
</div>
{% endblock %}
```

**Step 5: Create `web/templates/page_created.html`**

```html
{% extends "base.html" %}
{% block title %}Page Created{% endblock %}
{% block content %}
<div class="row justify-content-center">
  <div class="col-md-8">
    <h1 class="mb-4">{{ page_name }} — Created!</h1>

    <div class="alert alert-success">
      <strong>Save your edit link now.</strong> It won't be shown again.
    </div>

    <h5>Share this link (public view)</h5>
    <div class="input-group mb-4">
      <input type="text" class="form-control"
             value="{{ request.host_url }}view/{{ public_token }}" readonly id="pub-url">
      <button class="btn btn-outline-secondary"
              onclick="navigator.clipboard.writeText(document.getElementById('pub-url').value)">Copy</button>
    </div>

    <h5>Keep this link private (edit / manage)</h5>
    <div class="input-group mb-4">
      <input type="text" class="form-control"
             value="{{ request.host_url }}edit/{{ edit_token }}" readonly id="edit-url">
      <button class="btn btn-outline-secondary"
              onclick="navigator.clipboard.writeText(document.getElementById('edit-url').value)">Copy</button>
    </div>

    <a href="{{ url_for('page_edit', edit_token=edit_token) }}" class="btn btn-primary">
      Go to Edit Page →
    </a>
  </div>
</div>
{% endblock %}
```

**Step 6: Run to verify they pass**

```
pytest tests/web/test_pages.py -v
```
Expected: all PASS.

**Step 7: Commit**

```bash
git add web/routes.py web/templates/page_new.html web/templates/page_created.html tests/web/test_pages.py
git commit -m "feat: page creation routes and templates"
```

---

### Task 8: Web Routes — Public View

**Files:**
- Modify: `web/routes.py`
- Create: `web/templates/page_view.html`
- Test: `tests/web/test_pages.py`

**Step 1: Write the failing tests**

Add to `tests/web/test_pages.py`:

```python
def test_view_page_not_found(client):
    resp = client.get("/view/doesnotexist")
    assert resp.status_code == 404


def test_view_page_disabled(client):
    from db.models import create_user_page, get_db
    import sqlite3
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("Disabled", db_path)
    conn = get_db(db_path)
    conn.execute("UPDATE user_pages SET active=0 WHERE public_token=?", (pub,))
    conn.commit()
    conn.close()
    resp = client.get(f"/view/{pub}")
    assert resp.status_code == 404


def test_view_page_ok(client):
    from db.models import create_user_page
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("My Page", db_path)
    resp = client.get(f"/view/{pub}")
    assert resp.status_code == 200
    assert b"My Page" in resp.data
```

**Step 2: Run to verify they fail**

```
pytest tests/web/test_pages.py::test_view_page_not_found tests/web/test_pages.py::test_view_page_ok -v
```
Expected: FAIL.

**Step 3: Add route to `web/routes.py`**

```python
    @app.route("/view/<public_token>")
    def page_view(public_token):
        db_path = current_app.config["DB_PATH"]
        from db.models import get_page_by_public_token, get_page_gauges
        from flask import abort
        page = get_page_by_public_token(public_token, db_path)
        if not page or not page["active"]:
            abort(404)
        gauges = get_page_gauges(page["id"], db_path)
        return render_template("page_view.html", page=page, gauges=gauges)
```

**Step 4: Create `web/templates/page_view.html`**

```html
{% extends "base.html" %}
{% block title %}{{ page.page_name }}{% endblock %}
{% block head %}<meta http-equiv="refresh" content="300">{% endblock %}
{% block content %}
<h1 class="mb-1">{{ page.page_name }}</h1>
<p class="text-muted small mb-4">Updates every 5 minutes.</p>

{% if gauges %}
  {% for g in gauges %}
  {% set badge = "success" if g.severity == "Normal"
                 else "warning" if g.severity in ("Action", "Minor")
                 else "danger" if g.severity in ("Moderate", "Major")
                 else "secondary" %}
  <div class="card mb-4">
    <div class="card-header d-flex justify-content-between align-items-center">
      <strong>{{ g.station_name }}</strong>
      <span class="badge bg-{{ badge }}">{{ g.severity or "No data" }}</span>
    </div>
    <div class="card-body text-center">
      <img src="https://water.noaa.gov/resources/hydrographs/{{ g.lid | lower }}_hg.png"
           alt="Hydrograph for {{ g.station_name }}"
           class="img-fluid"
           style="max-width:600px;">
      {% if g.current_stage is not none %}
      <p class="mt-2 mb-0">Current stage: <strong>{{ "%.2f"|format(g.current_stage) }} ft</strong>
        {% if g.last_polled_at %}<span class="text-muted small">(as of {{ g.last_polled_at }})</span>{% endif %}
      </p>
      {% endif %}
    </div>
    <div class="card-footer text-end">
      <a href="https://water.noaa.gov/gauges/{{ g.lid | lower }}" target="_blank" class="btn btn-sm btn-outline-secondary">
        Full NOAA Page ↗
      </a>
    </div>
  </div>
  {% endfor %}
{% else %}
<div class="alert alert-info">No gauges added yet. Ask the page owner to add some.</div>
{% endif %}

<hr>
<p class="text-muted small">
  Want alerts when conditions change?
  <a href="{{ request.url }}#subscribe">Subscribe to notifications</a>.
</p>
{% endblock %}
```

**Step 5: Run to verify they pass**

```
pytest tests/web/test_pages.py -v
```
Expected: all PASS.

**Step 6: Commit**

```bash
git add web/routes.py web/templates/page_view.html tests/web/test_pages.py
git commit -m "feat: public landing page view for user gauges"
```

---

### Task 9: Web Routes — Edit Page (Gauge Management + Subscribe)

**Files:**
- Modify: `web/routes.py`
- Create: `web/templates/page_edit.html`
- Test: `tests/web/test_pages.py`

**Step 1: Write the failing tests**

Add to `tests/web/test_pages.py`:

```python
def test_edit_page_not_found(client):
    resp = client.get("/edit/doesnotexist")
    assert resp.status_code == 404


def test_edit_page_ok(client):
    from db.models import create_user_page
    db_path = client.application.config["DB_PATH"]
    _, edit = create_user_page("Test", db_path)
    resp = client.get(f"/edit/{edit}")
    assert resp.status_code == 200
    assert b"Test" in resp.data


def test_add_gauge_to_page(client):
    from db.models import create_user_page, get_page_by_edit_token, get_page_gauges
    from unittest.mock import patch
    db_path = client.application.config["DB_PATH"]
    _, edit = create_user_page("Test", db_path)
    page = get_page_by_edit_token(edit, db_path)

    mock_meta = {
        "station_name": "Ohio River at McAlpine Upper",
        "action_stage": 21.0, "minor_flood_stage": 23.0,
        "moderate_flood_stage": 30.0, "major_flood_stage": 38.0,
    }
    with patch("web.routes.fetch_gauge_metadata", return_value=mock_meta):
        resp = client.post(f"/edit/{edit}/gauges/add",
                           data={"lid": "MLUK2"}, follow_redirects=True)
    assert resp.status_code == 200
    gauges = get_page_gauges(page["id"], db_path)
    assert len(gauges) == 1
    assert gauges[0]["lid"] == "MLUK2"


def test_add_gauge_invalid_lid(client):
    from db.models import create_user_page
    from unittest.mock import patch
    db_path = client.application.config["DB_PATH"]
    _, edit = create_user_page("Test", db_path)
    with patch("web.routes.fetch_gauge_metadata", return_value=None):
        resp = client.post(f"/edit/{edit}/gauges/add",
                           data={"lid": "BADLID"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"not found" in resp.data.lower() or b"invalid" in resp.data.lower()


def test_subscribe_to_page(client):
    from db.models import create_user_page, get_page_by_edit_token, get_active_page_subscribers
    db_path = client.application.config["DB_PATH"]
    _, edit = create_user_page("Test", db_path)
    page = get_page_by_edit_token(edit, db_path)
    resp = client.post(f"/edit/{edit}/subscribe",
                       data={"channel": "sms", "channel_id": "+15025551234", "display_name": "Alice"},
                       follow_redirects=True)
    assert resp.status_code == 200
    subs = get_active_page_subscribers(page["id"], db_path)
    assert len(subs) == 1
```

**Step 2: Run to verify they fail**

```
pytest tests/web/test_pages.py -k "edit or gauge or subscribe" -v
```
Expected: FAIL.

**Step 3: Add edit routes to `web/routes.py`**

Add this import at the top of `register_routes`:
```python
from monitor.noaa_client import fetch_gauge_metadata
```

Then add these routes inside `register_routes(app)`:

```python
    @app.route("/edit/<edit_token>")
    def page_edit(edit_token):
        db_path = current_app.config["DB_PATH"]
        from db.models import get_page_by_edit_token, get_page_gauges, get_active_page_subscribers
        from flask import abort
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        gauges = get_page_gauges(page["id"], db_path)
        subscribers = get_active_page_subscribers(page["id"], db_path)
        return render_template("page_edit.html", page=page, gauges=gauges,
                               subscribers=subscribers, edit_token=edit_token)

    @app.route("/edit/<edit_token>/gauges/add", methods=["POST"])
    def page_add_gauge(edit_token):
        db_path = current_app.config["DB_PATH"]
        from db.models import (get_page_by_edit_token, get_or_create_noaa_gauge,
                                link_page_gauge)
        from flask import abort
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        lid = request.form.get("lid", "").strip().upper()
        if not lid:
            flash("Gauge ID is required.", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))
        meta = fetch_gauge_metadata(lid)
        if meta is None:
            flash(f"Gauge '{lid}' not found in the NOAA database.", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))
        gauge_id = get_or_create_noaa_gauge(
            lid, meta["station_name"],
            meta["action_stage"], meta["minor_flood_stage"],
            meta["moderate_flood_stage"], meta["major_flood_stage"],
            db_path
        )
        link_page_gauge(page["id"], gauge_id, db_path)
        flash(f"Added {meta['station_name']}.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))

    @app.route("/edit/<edit_token>/gauges/remove", methods=["POST"])
    def page_remove_gauge(edit_token):
        db_path = current_app.config["DB_PATH"]
        from db.models import get_page_by_edit_token, unlink_page_gauge
        from flask import abort
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        gauge_id = request.form.get("gauge_id", type=int)
        if gauge_id:
            unlink_page_gauge(page["id"], gauge_id, db_path)
            flash("Gauge removed.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))

    @app.route("/edit/<edit_token>/subscribe", methods=["POST"])
    def page_subscribe(edit_token):
        db_path = current_app.config["DB_PATH"]
        from db.models import get_page_by_edit_token, add_page_subscriber
        from flask import abort
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        channel = request.form.get("channel", "").strip()
        channel_id = request.form.get("channel_id", "").strip()
        display_name = request.form.get("display_name", "").strip()
        if not channel or not channel_id:
            flash("Channel and channel ID are required.", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))
        if channel in ("sms", "whatsapp"):
            channel_id = normalize_e164(channel_id)
        add_page_subscriber(page["id"], channel, channel_id, display_name, db_path)
        flash("Subscribed to alerts for this page.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))

    @app.route("/edit/<edit_token>/unsubscribe", methods=["POST"])
    def page_unsubscribe(edit_token):
        db_path = current_app.config["DB_PATH"]
        from db.models import get_page_by_edit_token, set_page_subscriber_status
        from flask import abort
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        channel = request.form.get("channel", "").strip()
        channel_id = request.form.get("channel_id", "").strip()
        new_status = request.form.get("status", "unsubscribed")
        if channel and channel_id:
            set_page_subscriber_status(page["id"], channel, channel_id, new_status, db_path)
            flash(f"Status updated to {new_status}.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))
```

**Step 4: Create `web/templates/page_edit.html`**

```html
{% extends "base.html" %}
{% block title %}Edit — {{ page.page_name }}{% endblock %}
{% block content %}
<h1 class="mb-4">{{ page.page_name }} <small class="text-muted fs-6">Edit Page</small></h1>

<div class="row g-4">
  <div class="col-md-6">
    <div class="card">
      <div class="card-header">Gauges</div>
      <div class="card-body">
        <form method="post" action="{{ url_for('page_add_gauge', edit_token=edit_token) }}" class="input-group mb-3">
          <input type="text" name="lid" class="form-control text-uppercase"
                 placeholder="NOAA Gauge ID (e.g. MLUK2)" required>
          <button class="btn btn-primary" type="submit">Add</button>
        </form>
        {% if gauges %}
        <ul class="list-group list-group-flush">
          {% for g in gauges %}
          <li class="list-group-item d-flex justify-content-between align-items-center">
            <span>{{ g.station_name }} <span class="text-muted small">({{ g.lid }})</span></span>
            <form method="post" action="{{ url_for('page_remove_gauge', edit_token=edit_token) }}" class="m-0">
              <input type="hidden" name="gauge_id" value="{{ g.id }}">
              <button class="btn btn-sm btn-outline-danger">Remove</button>
            </form>
          </li>
          {% endfor %}
        </ul>
        {% else %}
        <p class="text-muted mb-0">No gauges yet. Enter a NOAA gauge ID above.</p>
        {% endif %}
      </div>
    </div>
  </div>

  <div class="col-md-6">
    <div class="card mb-3">
      <div class="card-header">Subscribe to Alerts</div>
      <div class="card-body">
        <form method="post" action="{{ url_for('page_subscribe', edit_token=edit_token) }}">
          <div class="mb-2">
            <input type="text" name="display_name" class="form-control" placeholder="Your name (optional)">
          </div>
          <div class="mb-2">
            <select name="channel" class="form-select">
              <option value="sms">SMS</option>
              <option value="whatsapp">WhatsApp</option>
              <option value="telegram">Telegram</option>
              <option value="facebook">Facebook Messenger</option>
            </select>
          </div>
          <div class="input-group">
            <input type="text" name="channel_id" class="form-control"
                   placeholder="Phone / Chat ID / PSID" required>
            <button class="btn btn-success" type="submit">Subscribe</button>
          </div>
        </form>
      </div>
    </div>

    {% if subscribers %}
    <div class="card">
      <div class="card-header">Active Subscribers</div>
      <ul class="list-group list-group-flush">
        {% for s in subscribers %}
        <li class="list-group-item d-flex justify-content-between align-items-center">
          <span>{{ s.display_name or s.channel_id }} <span class="text-muted small">({{ s.channel }})</span></span>
          <form method="post" action="{{ url_for('page_unsubscribe', edit_token=edit_token) }}" class="m-0">
            <input type="hidden" name="channel" value="{{ s.channel }}">
            <input type="hidden" name="channel_id" value="{{ s.channel_id }}">
            <input type="hidden" name="status" value="unsubscribed">
            <button class="btn btn-sm btn-outline-secondary">Remove</button>
          </form>
        </li>
        {% endfor %}
      </ul>
    </div>
    {% endif %}
  </div>
</div>

<div class="mt-4">
  <a href="{{ url_for('page_view', public_token=page.public_token) }}" class="btn btn-outline-primary">
    View Public Page →
  </a>
</div>
{% endblock %}
```

**Step 5: Run to verify they pass**

```
pytest tests/web/test_pages.py -v
```
Expected: all PASS.

**Step 6: Commit**

```bash
git add web/routes.py web/templates/page_edit.html tests/web/test_pages.py
git commit -m "feat: edit page routes for gauge management and subscriptions"
```

---

### Task 10: Admin Pages

**Files:**
- Modify: `web/routes.py`
- Modify: `web/templates/base.html`
- Create: `web/templates/admin_pages.html`
- Test: `tests/web/test_pages.py`

**Step 1: Write the failing test**

Add to `tests/web/test_pages.py`:

```python
def test_admin_pages_list(client):
    from db.models import create_user_page
    db_path = client.application.config["DB_PATH"]
    create_user_page("Alpha", db_path)
    create_user_page("Beta", db_path)
    resp = client.get("/admin/pages")
    assert resp.status_code == 200
    assert b"Alpha" in resp.data
    assert b"Beta" in resp.data


def test_admin_toggle_page(client):
    from db.models import create_user_page, get_page_by_public_token
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("Togglable", db_path)
    page = get_page_by_public_token(pub, db_path)
    resp = client.post(f"/admin/pages/{page['id']}/toggle", follow_redirects=True)
    assert resp.status_code == 200
    updated = get_page_by_public_token(pub, db_path)
    assert updated["active"] == 0
```

**Step 2: Run to verify they fail**

```
pytest tests/web/test_pages.py::test_admin_pages_list tests/web/test_pages.py::test_admin_toggle_page -v
```
Expected: FAIL.

**Step 3: Add admin routes to `web/routes.py`**

```python
    @app.route("/admin/pages")
    def admin_pages():
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        pages = conn.execute("""
            SELECT up.*,
                   COUNT(DISTINCT png.noaa_gauge_id) AS gauge_count,
                   COUNT(DISTINCT ps.id) AS subscriber_count
            FROM user_pages up
            LEFT JOIN page_noaa_gauges png ON png.page_id = up.id
            LEFT JOIN page_subscribers ps ON ps.page_id = up.id AND ps.status='active'
            GROUP BY up.id
            ORDER BY up.created_at DESC
        """).fetchall()
        conn.close()
        return render_template("admin_pages.html", pages=pages)

    @app.route("/admin/pages/<int:page_id>/toggle", methods=["POST"])
    def admin_toggle_page(page_id):
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        conn.execute("UPDATE user_pages SET active = 1 - active WHERE id=?", (page_id,))
        conn.commit()
        conn.close()
        flash("Page status updated.", "success")
        return redirect(url_for("admin_pages"))
```

**Step 4: Add "Admin" link to `web/templates/base.html`**

In the `<div class="navbar-nav">` block, add after the Broadcast link:
```html
      <a class="nav-link" href="/admin/pages">Admin</a>
```

**Step 5: Create `web/templates/admin_pages.html`**

```html
{% extends "base.html" %}
{% block title %}Admin — Pages{% endblock %}
{% block content %}
<h1 class="mb-4">User Pages</h1>
{% if pages %}
<table class="table table-striped">
  <thead><tr>
    <th>Name</th><th>Gauges</th><th>Subscribers</th>
    <th>Created</th><th>Status</th><th></th>
  </tr></thead>
  <tbody>
  {% for p in pages %}
  <tr class="{{ '' if p.active else 'table-secondary text-muted' }}">
    <td>
      {{ p.page_name }}<br>
      <small class="text-muted">
        <a href="{{ url_for('page_view', public_token=p.public_token) }}" target="_blank">View ↗</a>
      </small>
    </td>
    <td>{{ p.gauge_count }}</td>
    <td>{{ p.subscriber_count }}</td>
    <td><small>{{ p.created_at }}</small></td>
    <td>
      <span class="badge bg-{{ 'success' if p.active else 'secondary' }}">
        {{ 'Active' if p.active else 'Disabled' }}
      </span>
    </td>
    <td>
      <form method="post" action="{{ url_for('admin_toggle_page', page_id=p.id) }}">
        <button class="btn btn-sm btn-outline-{{ 'danger' if p.active else 'success' }}">
          {{ 'Disable' if p.active else 'Enable' }}
        </button>
      </form>
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="text-muted">No pages created yet.</p>
{% endif %}
<a href="{{ url_for('page_new') }}" class="btn btn-primary">+ Create New Page</a>
{% endblock %}
```

**Step 6: Run to verify they pass**

```
pytest tests/web/test_pages.py -v
```
Expected: all PASS. Run the full test suite to check nothing is broken:
```
pytest
```

**Step 7: Commit**

```bash
git add web/routes.py web/templates/base.html web/templates/admin_pages.html tests/web/test_pages.py
git commit -m "feat: admin pages list and toggle with nav link"
```

---

### Task 11: Extend Webhooks for Page Subscriber Self-Service

**Files:**
- Modify: `web/routes.py`
- Test: `tests/web/test_pages.py`

Subscribers who don't have the edit URL can text `PAUSE`, `RESUME`, or `STOP` to manage their page subscriptions via their existing channel.

**Step 1: Write the failing tests**

Add to `tests/web/test_pages.py`:

```python
def test_twilio_pause_page_subscriber(client):
    from db.models import (create_user_page, get_page_by_public_token,
                           add_page_subscriber, get_db)
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("Test", db_path)
    page = get_page_by_public_token(pub, db_path)
    add_page_subscriber(page["id"], "sms", "+15025551234", "Alice", db_path)

    resp = client.post("/webhook/twilio",
                       data={"From": "+15025551234", "Body": "PAUSE", "To": "+18005550000"})
    assert resp.status_code == 200
    conn = get_db(db_path)
    row = conn.execute(
        "SELECT status FROM page_subscribers WHERE channel_id=?", ("+15025551234",)
    ).fetchone()
    conn.close()
    assert row["status"] == "paused"


def test_twilio_resume_page_subscriber(client):
    from db.models import (create_user_page, get_page_by_public_token,
                           add_page_subscriber, set_page_subscriber_status, get_db)
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("Test", db_path)
    page = get_page_by_public_token(pub, db_path)
    add_page_subscriber(page["id"], "sms", "+15025551234", "Bob", db_path)
    set_page_subscriber_status(page["id"], "sms", "+15025551234", "paused", db_path)

    resp = client.post("/webhook/twilio",
                       data={"From": "+15025551234", "Body": "RESUME", "To": "+18005550000"})
    assert resp.status_code == 200
    conn = get_db(db_path)
    row = conn.execute(
        "SELECT status FROM page_subscribers WHERE channel_id=?", ("+15025551234",)
    ).fetchone()
    conn.close()
    assert row["status"] == "active"
```

**Step 2: Run to verify they fail**

```
pytest tests/web/test_pages.py::test_twilio_pause_page_subscriber tests/web/test_pages.py::test_twilio_resume_page_subscriber -v
```
Expected: FAIL.

**Step 3: Extend the Twilio webhook in `web/routes.py`**

Inside `webhook_twilio()`, after the existing `elif body in ("STOP", "UNSUBSCRIBE"):` block and before `conn.close()`, add:

```python
        # Page subscriber self-service
        if body == "PAUSE":
            conn.execute(
                "UPDATE page_subscribers SET status='paused' WHERE channel=? AND channel_id=?",
                (channel, clean_from)
            )
            conn.commit()
        elif body == "RESUME":
            conn.execute(
                "UPDATE page_subscribers SET status='active' WHERE channel=? AND channel_id=?",
                (channel, clean_from)
            )
            conn.commit()
        elif body in ("STOP", "UNSUBSCRIBE"):
            conn.execute(
                "UPDATE page_subscribers SET status='unsubscribed' WHERE channel=? AND channel_id=?",
                (channel, clean_from)
            )
            conn.commit()
```

**Step 4: Run to verify they pass**

```
pytest tests/web/test_pages.py -v
```

Run the full suite to confirm nothing regressed:
```
pytest
```
Expected: all PASS.

**Step 5: Commit**

```bash
git add web/routes.py tests/web/test_pages.py
git commit -m "feat: twilio webhook handles PAUSE/RESUME for page subscribers"
```

---

## Verification

After all tasks are complete, run the full test suite:

```
pytest -v
```

Then do a manual smoke test:
1. `python service.py debug`
2. Visit `http://localhost:5743/pages/new` — create a page
3. On the edit page, add gauge `MLUK2`
4. Visit the public view URL — confirm the NOAA image loads and stage is shown
5. Visit `http://localhost:5743/admin/pages` — confirm the page appears and can be toggled
