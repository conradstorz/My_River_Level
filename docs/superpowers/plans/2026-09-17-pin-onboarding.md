# Pin-on-a-Map Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A member of the public drops a pin on a map, the system proposes the gauges on that stretch of river, they confirm, and their Telegram chat receives alerts for it — with no admin involvement and no account.

**Architecture:** The existing `user_pages` / `page_sites` / `page_noaa_gauges` / `page_subscribers` model is reused: a pin creates a page owned by a Telegram chat, the chosen gauges are provisioned into `sites` / `noaa_gauges` with `origin='user'`, and the unchanged pollers pick them up. Discovery is a pure module calling the USGS NLDI network API with a bounding-box fallback. A per-page sensitivity dial is applied in the dispatcher at routing time, so one poll serves every user of a gauge. An hourly sweep deactivates user-origin sources nobody references any more.

**Tech Stack:** Python 3.11, Flask, psycopg2 (PostgreSQL), `requests`, `dataretrieval.nwis`, python-telegram-bot 22, Leaflet 1.9.4 + OpenStreetMap tiles, pytest.

Spec: `docs/superpowers/specs/2026-09-17-pin-onboarding-design.md`

## Global Constraints

- **Running tests:** plain `pytest` cannot reach the database from this machine. Every test run goes through the Docker overlay with `--build`, one command per Bash call, no `&&`/`|`/`;`:
  `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/db/test_pin_schema.py -v`
  Parallel runs must set a distinct `TEST_DB_SUFFIX` (e.g. `TEST_DB_SUFFIX=_t3 docker compose ...`).
- **No network in tests.** Every `requests`, `nwis`, and Telegram call is patched.
- **Additive migrations only.** New columns go in `MIGRATION_STATEMENTS` in `db/models.py` as `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`; never drop or rewrite columns.
- **Column names:** the admin kill switch is `active` (INTEGER 0/1) on `sites`, `noaa_gauges`, and `user_pages`. The new per-page lifecycle column is `status` ('pending'|'active'|'paused'|'stopped'). Sensitivity values are exactly `floods`, `unusual`, `all`.
- **Alert kinds** (queue item `type`): `transition`, `trend`, `reminder`, `noaa_transition`. USGS severities are the strings `"SEVERE LOW"`, `"LOW"`, `"NORMAL"`, `"HIGH"`, `"SEVERE HIGH"`, `"UNKNOWN"` (space, not underscore).
- **Module naming:** the pin flow module is `monitor/pin_discovery.py`; `monitor/gauge_discovery.py` already exists and is the name-search module — do not touch it.
- **Every DB helper** follows the file's pattern: `conn = get_conn(db_path)`, `cur = conn.cursor()`, `try/finally` closing both, dict rows.
- **Commit after every task** with a conventional-commit message ending in `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `db/models.py` (modify) | Migrations, default settings, pin-page helpers (`create_pin_page`, `get_page_for_chat`, `bind_page_to_chat`, `save_pin`, `set_page_sensitivity`, `set_page_status`), routing queries gain `status`/`sensitivity`, `get_all_noaa_gauges(active_only=)`, `get_or_create_noaa_gauge(origin=)`, site/page reference counts |
| `monitor/scheduler.py` (modify) | `alert_allowed(sensitivity, alert_type, severity)`; hourly call to the retirement sweep |
| `monitor/dispatcher.py` (modify) | Applies `alert_allowed` per subscriber; new `direct` queue item type for one-off messages to a chat |
| `monitor/retirement.py` (create) | `sweep(db_path)` — deactivate unreferenced user-origin sources, delete stale pending pages |
| `monitor/noaa_client.py` (modify) | `gauges_near(lat, lon, radius_miles)` via the NWPS `/gauges` bbox listing |
| `monitor/pin_discovery.py` (create) | `discover(lat, lon, *, reach_km, fallback_radius_miles)` → `Discovery`; NLDI snap + navigation, parameter check, NOAA match, bbox fallback |
| `web/ratelimit.py` (create) | `RateLimiter(limit, per_seconds).allow(key)` in-process token window |
| `web/routes.py` (modify) | `/pin`, `/pin/<token>`, `/pin/<token>/discover`, `/pin/<token>/save`, `/edit/<token>/sensitivity`; admin badges; settings fields |
| `web/templates/pin.html` (create) | Standalone Leaflet map page (no admin nav) |
| `web/templates/page_edit.html` (modify) | Sensitivity control, "move the pin" link |
| `web/templates/sites.html`, `admin_pages.html` (modify) | Origin badge, reference count, pin/owner/status columns |
| `web/auth.py` (modify) | Add pin endpoints to `PUBLIC_ENDPOINTS` |
| `monitor/adapters/telegram_commands.py` (create) | `PinCommands(db_path)` — sync helpers + async handlers for `/start`, `/settings`, `/sensitivity`, `/sources`, `/pause`, `/resume`, `/stop`, callback queries |
| `monitor/adapters/telegram.py` (modify) | Wire `PinCommands`, record bot username via `post_init` |
| `CLAUDE.md` (modify) | Architecture notes for the pin flow |

Tests: `tests/db/test_pin_schema.py`, `tests/db/test_pin_pages.py`, `tests/db/test_retirement.py`, `tests/monitor/test_sensitivity.py`, `tests/monitor/test_dispatcher.py` (extend), `tests/monitor/test_noaa_client.py` (extend), `tests/monitor/test_pin_discovery.py`, `tests/web/test_ratelimit.py`, `tests/web/test_pin.py`, `tests/monitor/test_telegram_commands.py`, `tests/web/test_sites.py` + `tests/web/test_pages.py` (extend).

---

### Task 1: Schema migrations and settings

**Files:**
- Modify: `db/models.py` (`DEFAULT_SETTINGS`, `MIGRATION_STATEMENTS`, `get_or_create_noaa_gauge`, `get_all_noaa_gauges`)
- Modify: `monitor/noaa_polling.py:96`, `monitor/forecast_polling.py:55` (call `get_all_noaa_gauges(db_path, active_only=True)`)
- Test: `tests/db/test_pin_schema.py`

**Interfaces:**
- Produces: columns `user_pages.owner_chat_id BIGINT NULL`, `user_pages.pin_lat/pin_lon DOUBLE PRECISION NULL`, `user_pages.river_name TEXT NULL`, `user_pages.sensitivity TEXT NOT NULL DEFAULT 'unusual'`, `user_pages.status TEXT NOT NULL DEFAULT 'active'`, `sites.origin TEXT NOT NULL DEFAULT 'admin'`, `noaa_gauges.origin TEXT NOT NULL DEFAULT 'admin'`, `noaa_gauges.active INTEGER NOT NULL DEFAULT 1`.
- Produces: settings `discovery_reach_km` = "50", `public_base_url` = "", `telegram_bot_username` = "".
- Produces: `get_or_create_noaa_gauge(lid, station_name, action_stage, minor_stage, moderate_stage, major_stage, db_path=None, origin="admin") -> int` (reactivates an existing gauge); `get_all_noaa_gauges(db_path=None, active_only=False) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/db/test_pin_schema.py
import psycopg2
import pytest

from db.models import (get_db, get_setting, get_or_create_noaa_gauge,
                       get_all_noaa_gauges)


def _columns(tmp_db, table):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name, column_default FROM information_schema.columns "
        "WHERE table_name=%s", (table,))
    rows = {r["column_name"]: r["column_default"] for r in cur.fetchall()}
    cur.close()
    conn.close()
    return rows


def test_user_pages_gains_pin_columns(tmp_db):
    cols = _columns(tmp_db, "user_pages")
    for name in ("owner_chat_id", "pin_lat", "pin_lon", "river_name",
                 "sensitivity", "status"):
        assert name in cols
    assert "'unusual'" in cols["sensitivity"]
    assert "'active'" in cols["status"]


def test_sources_gain_origin_and_noaa_gains_active(tmp_db):
    assert "'admin'" in _columns(tmp_db, "sites")["origin"]
    noaa = _columns(tmp_db, "noaa_gauges")
    assert "'admin'" in noaa["origin"]
    assert noaa["active"] == "1"


def test_sensitivity_is_constrained(tmp_db):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    try:
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "INSERT INTO user_pages (public_token, edit_token, sensitivity) "
                "VALUES ('a', 'b', 'loud')")
    finally:
        cur.close()
        conn.close()


def test_new_settings_seeded(tmp_db):
    assert get_setting("discovery_reach_km", tmp_db) == "50"
    assert get_setting("public_base_url", tmp_db) == ""
    assert get_setting("telegram_bot_username", tmp_db) == ""


def test_get_or_create_noaa_gauge_records_origin(tmp_db):
    get_or_create_noaa_gauge("MLUK2", "McAlpine", 21.0, 23.0, 30.0, 38.0,
                             tmp_db, origin="user")
    gauge = get_all_noaa_gauges(tmp_db)[0]
    assert gauge["origin"] == "user"
    assert gauge["active"] == 1


def test_get_or_create_reactivates_a_retired_gauge(tmp_db):
    get_or_create_noaa_gauge("MLUK2", "McAlpine", None, None, None, None, tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("UPDATE noaa_gauges SET active=0 WHERE lid='MLUK2'")
    conn.commit()
    cur.close()
    conn.close()
    get_or_create_noaa_gauge("MLUK2", "McAlpine", None, None, None, None, tmp_db)
    assert get_all_noaa_gauges(tmp_db)[0]["active"] == 1


def test_get_all_noaa_gauges_active_only_skips_inactive(tmp_db):
    get_or_create_noaa_gauge("AAAA1", "A", None, None, None, None, tmp_db)
    get_or_create_noaa_gauge("BBBB1", "B", None, None, None, None, tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("UPDATE noaa_gauges SET active=0 WHERE lid='BBBB1'")
    conn.commit()
    cur.close()
    conn.close()
    assert {g["lid"] for g in get_all_noaa_gauges(tmp_db)} == {"AAAA1", "BBBB1"}
    assert [g["lid"] for g in get_all_noaa_gauges(tmp_db, active_only=True)] == ["AAAA1"]
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/db/test_pin_schema.py -v`
Expected: FAIL — KeyError on `owner_chat_id`, `get_setting` returns None, `TypeError: unexpected keyword argument 'origin'`.

- [ ] **Step 3: Add settings and migrations**

In `db/models.py`, append to `DEFAULT_SETTINGS`:

```python
    "forecast_poll_hours": "6",
    # Pin onboarding
    "discovery_reach_km": "50",
    "public_base_url": "",
    "telegram_bot_username": "",
```

Append to `MIGRATION_STATEMENTS`:

```python
    # Pin-on-a-map onboarding (spec 2026-09-17). A page owned by a Telegram
    # chat carries its pin, river, sensitivity dial and lifecycle status;
    # sources provisioned by users are marked so the retirement sweep can
    # deactivate them once nobody references them, and never touch admin rows.
    "ALTER TABLE user_pages ADD COLUMN IF NOT EXISTS owner_chat_id BIGINT",
    "ALTER TABLE user_pages ADD COLUMN IF NOT EXISTS pin_lat DOUBLE PRECISION",
    "ALTER TABLE user_pages ADD COLUMN IF NOT EXISTS pin_lon DOUBLE PRECISION",
    "ALTER TABLE user_pages ADD COLUMN IF NOT EXISTS river_name TEXT",
    "ALTER TABLE user_pages ADD COLUMN IF NOT EXISTS sensitivity TEXT NOT NULL DEFAULT 'unusual' "
    "CHECK (sensitivity IN ('floods', 'unusual', 'all'))",
    "ALTER TABLE user_pages ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active' "
    "CHECK (status IN ('pending', 'active', 'paused', 'stopped'))",
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'admin' "
    "CHECK (origin IN ('admin', 'user'))",
    "ALTER TABLE noaa_gauges ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'admin' "
    "CHECK (origin IN ('admin', 'user'))",
    "ALTER TABLE noaa_gauges ADD COLUMN IF NOT EXISTS active INTEGER NOT NULL DEFAULT 1",
    "CREATE INDEX IF NOT EXISTS idx_user_pages_owner_chat ON user_pages (owner_chat_id)",
```

Replace `get_or_create_noaa_gauge` and `get_all_noaa_gauges`:

```python
def get_or_create_noaa_gauge(lid, station_name, action_stage, minor_stage,
                              moderate_stage, major_stage, db_path=None,
                              origin="admin"):
    """Insert gauge if not present; return its id.

    An existing gauge keeps its original origin but is reactivated: a user
    re-selecting a gauge the retirement sweep switched off must get polling
    back without admin help.
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO noaa_gauges
               (lid, station_name, action_stage, minor_flood_stage,
                moderate_flood_stage, major_flood_stage, origin)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (lid) DO UPDATE SET active = 1""",
            (lid, station_name, action_stage, minor_stage, moderate_stage,
             major_stage, origin)
        )
        conn.commit()
        cur.execute("SELECT id FROM noaa_gauges WHERE lid=%s", (lid,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return row["id"]


def get_all_noaa_gauges(db_path=None, active_only=False):
    """Return noaa_gauges rows as dicts; `active_only` skips retired gauges."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        if active_only:
            cur.execute("SELECT * FROM noaa_gauges WHERE active=1 ORDER BY id")
        else:
            cur.execute("SELECT * FROM noaa_gauges ORDER BY id")
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]
```

In `monitor/noaa_polling.py` `_poll`: `gauges = get_all_noaa_gauges(self.db_path, active_only=True)`.
In `monitor/forecast_polling.py` `_poll`: `gauges = get_all_noaa_gauges(self.db_path, active_only=True)`.

- [ ] **Step 4: Run to verify pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/db tests/monitor/test_noaa_polling.py tests/monitor/test_forecast_polling.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add db/models.py monitor/noaa_polling.py monitor/forecast_polling.py tests/db/test_pin_schema.py
git commit -m "feat(db): pin-page columns, source origin, NOAA gauge active flag"
```

---

### Task 2: Sensitivity filter and status-aware routing

**Files:**
- Modify: `monitor/scheduler.py` (add `alert_allowed`)
- Modify: `db/models.py` (`get_page_subscribers_for_site`, `get_page_subscribers_for_gauge`)
- Modify: `monitor/dispatcher.py` (filter per subscriber; add `direct` item type)
- Test: `tests/monitor/test_sensitivity.py`, `tests/monitor/test_dispatcher.py`

**Interfaces:**
- Produces: `alert_allowed(sensitivity: str, alert_type: str, severity: str | None) -> bool` in `monitor/scheduler.py`. `alert_type` is a queue item type; `severity` is the new/current USGS severity for `transition`/`reminder` and `None` for `trend`/`noaa_transition`.
- Produces: rows from `get_page_subscribers_for_site` / `get_page_subscribers_for_gauge` carry a `sensitivity` key and exclude pages whose `status` is not `'active'`.
- Produces: queue item `{"type": "direct", "data": {"channel": "telegram", "channel_id": "123", "message": "..."}}` — dispatcher sends it through the matching adapter with no subscriber lookup. Task 8 and Task 10 enqueue these.

- [ ] **Step 1: Write failing tests for `alert_allowed`**

```python
# tests/monitor/test_sensitivity.py
import pytest
from monitor.scheduler import alert_allowed


@pytest.mark.parametrize("sensitivity,alert_type,severity,expected", [
    # floods: NOAA category changes and SEVERE HIGH only
    ("floods", "noaa_transition", None, True),
    ("floods", "transition", "SEVERE HIGH", True),
    ("floods", "reminder", "SEVERE HIGH", True),
    ("floods", "transition", "HIGH", False),
    ("floods", "transition", "LOW", False),
    ("floods", "transition", "SEVERE LOW", False),
    ("floods", "transition", "NORMAL", False),
    ("floods", "trend", None, False),
    # unusual: adds LOW / HIGH / SEVERE LOW and the return to NORMAL
    ("unusual", "transition", "HIGH", True),
    ("unusual", "transition", "LOW", True),
    ("unusual", "transition", "SEVERE LOW", True),
    ("unusual", "reminder", "LOW", True),
    ("unusual", "transition", "NORMAL", True),
    ("unusual", "trend", None, False),
    ("unusual", "noaa_transition", None, True),
    # all: everything
    ("all", "trend", None, True),
    ("all", "transition", "NORMAL", True),
    ("all", "noaa_transition", None, True),
    # unknown dial behaves like the default 'unusual'
    ("weird", "trend", None, False),
    ("weird", "transition", "HIGH", True),
])
def test_alert_allowed_truth_table(sensitivity, alert_type, severity, expected):
    assert alert_allowed(sensitivity, alert_type, severity) is expected
```

A transition *to* NORMAL is the all-clear after an unusual level, so `unusual` and `all` receive it; `floods` does not, since the NOAA category change is its all-clear.

- [ ] **Step 2: Run to verify failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_sensitivity.py -v`
Expected: FAIL — `ImportError: cannot import name 'alert_allowed'`.

- [ ] **Step 3: Implement `alert_allowed`**

Add to `monitor/scheduler.py` after `get_reminder_interval_hours`:

```python
#: Which USGS severities each sensitivity level wants to hear about. Trend
#: (rise/fall rate) alerts are gated separately because they carry no
#: severity. NOAA flood-category changes reach every level.
_SEVERITIES_FOR = {
    "floods": {"SEVERE HIGH"},
    "unusual": {"SEVERE HIGH", "HIGH", "LOW", "SEVERE LOW", "NORMAL"},
    "all": {"SEVERE HIGH", "HIGH", "LOW", "SEVERE LOW", "NORMAL"},
}


def alert_allowed(sensitivity, alert_type, severity):
    """Return True if a page at `sensitivity` should receive this alert.

    Applied at dispatch time, so two pages watching the same gauge with
    different dials still cost one poll. An unrecognised dial is treated as
    the default 'unusual' rather than silencing the page.
    """
    level = sensitivity if sensitivity in _SEVERITIES_FOR else "unusual"
    if alert_type == "noaa_transition":
        return True
    if alert_type == "trend":
        return level == "all"
    if alert_type in ("transition", "reminder"):
        return severity in _SEVERITIES_FOR[level]
    return True
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_sensitivity.py -v`
Expected: PASS (20 cases).

- [ ] **Step 5: Write failing dispatcher tests**

Append to `tests/monitor/test_dispatcher.py`:

```python
def _set_page(tmp_db, page_id, **fields):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    for key, value in fields.items():
        cur.execute(f"UPDATE user_pages SET {key}=%s WHERE id=%s", (value, page_id))
    conn.commit()
    cur.close()
    conn.close()


def _transition_item(site_id, new_severity):
    return {"type": "transition", "data": {
        "site_id": site_id, "site_number": "12345678", "station_name": "Test",
        "previous_severity": "NORMAL", "new_severity": new_severity,
        "current_value": 1500.0, "unit": "cfs", "percentile": 91.0,
        "direction": "RISING",
    }}


def _adapter():
    mock_adapter = MagicMock()
    mock_adapter.channel = "telegram"
    mock_adapter.send.return_value = True
    return mock_adapter


def test_floods_page_does_not_receive_a_high_transition(tmp_db):
    init_db(tmp_db)
    page_id, site_id = _page_with_site(tmp_db)
    _set_page(tmp_db, page_id, sensitivity="floods")
    adapter = _adapter()
    q = queue.Queue()
    q.put(_transition_item(site_id, "HIGH"))
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_not_called()


def test_floods_page_receives_a_severe_high_transition(tmp_db):
    init_db(tmp_db)
    page_id, site_id = _page_with_site(tmp_db)
    _set_page(tmp_db, page_id, sensitivity="floods")
    adapter = _adapter()
    q = queue.Queue()
    q.put(_transition_item(site_id, "SEVERE HIGH"))
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_called_once()


def test_unusual_page_skips_trend_but_all_page_gets_it(tmp_db):
    init_db(tmp_db)
    page_id, site_id = _page_with_site(tmp_db)
    trend = {"type": "trend", "data": {
        "site_id": site_id, "site_number": "12345678", "station_name": "Test",
        "unit": "ft", "direction": "RISING", "delta": 2.5, "hours": 6.0,
        "start_value": 10.0, "end_value": 12.5,
    }}
    adapter = _adapter()
    q = queue.Queue()
    q.put(trend)
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_not_called()          # default dial is 'unusual'
    _set_page(tmp_db, page_id, sensitivity="all")
    q.put(trend)
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_called_once()


def test_paused_page_receives_nothing(tmp_db):
    init_db(tmp_db)
    page_id, site_id = _page_with_site(tmp_db)
    _set_page(tmp_db, page_id, status="paused")
    adapter = _adapter()
    q = queue.Queue()
    q.put(_transition_item(site_id, "SEVERE HIGH"))
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_not_called()


def test_paused_page_receives_no_noaa_transition(tmp_db):
    init_db(tmp_db)
    from db.models import (create_user_page, get_page_by_public_token,
                           get_or_create_noaa_gauge, link_page_gauge,
                           add_page_subscriber)
    pub, _ = create_user_page("P", tmp_db)
    page = get_page_by_public_token(pub, tmp_db)
    gauge_id = get_or_create_noaa_gauge("MLUK2", "M", 21.0, 23.0, 30.0, 38.0, tmp_db)
    link_page_gauge(page["id"], gauge_id, tmp_db)
    add_page_subscriber(page["id"], "telegram", "chat9", "S", tmp_db)
    _set_page(tmp_db, page["id"], status="paused")
    adapter = _adapter()
    q = queue.Queue()
    q.put({"type": "noaa_transition", "data": {
        "gauge_id": gauge_id, "lid": "MLUK2", "station_name": "M",
        "previous_severity": "Normal", "new_severity": "Action",
        "current_stage": 22.0}})
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_not_called()


def test_direct_item_sends_to_one_chat_without_subscribers(tmp_db):
    init_db(tmp_db)
    adapter = _adapter()
    q = queue.Queue()
    q.put({"type": "direct", "data": {
        "channel": "telegram", "channel_id": "777", "message": "hello"}})
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_called_once_with("777", "hello")
```

- [ ] **Step 6: Run to verify failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_dispatcher.py -v`
Expected: the six new tests FAIL (sends happen when they should not; `direct` logs "Unknown notification type").

- [ ] **Step 7: Make routing status-aware and sensitivity-aware**

In `db/models.py`, change both subscriber queries to select the page's dial and require the page to be live:

```python
def get_page_subscribers_for_gauge(gauge_id, db_path=None):
    """Return active page_subscribers (plus the page's sensitivity) for every
    live page linked to this gauge.

    A page is live when the admin flag `active` is set AND its lifecycle
    `status` is 'active' — paused, stopped and pending pages are silent.
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT ps.*, up.sensitivity FROM page_subscribers ps
               JOIN page_noaa_gauges png ON png.page_id = ps.page_id
               JOIN user_pages up ON up.id = ps.page_id
               WHERE png.noaa_gauge_id=%s AND ps.status='active'
                 AND up.active=1 AND up.status='active'""",
            (gauge_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]
```

Apply the same change to `get_page_subscribers_for_site`: `SELECT ps.*, up.sensitivity`, add `AND up.status='active'`, keep `ORDER BY ps.id`.

In `monitor/dispatcher.py` add the import:

```python
from monitor.scheduler import alert_allowed
```

Inside `run_once`, after the `broadcast` branch and before `if item["type"] == "transition":`, add:

```python
            if item["type"] == "direct":
                data = item["data"]
                adapter = self.adapters.get(data["channel"])
                if adapter is None:
                    logger.warning("No adapter for direct message on %s", data["channel"])
                    return
                try:
                    adapter.send(data["channel_id"], data["message"])
                except Exception:
                    logger.exception("Failed direct message to %s/%s",
                                     data["channel"], data["channel_id"])
                return
```

In the `noaa_transition` branch, make the first line of the `for sub in subscribers:` loop:

```python
                    if not alert_allowed(sub.get("sensitivity"), "noaa_transition", None):
                        continue
```

For the site branches, replace the final routing block with:

```python
            severity = None
            if item["type"] == "transition":
                severity = item["data"]["new_severity"]
            elif item["type"] == "reminder":
                severity = item["data"]["severity"]
            subscribers = get_page_subscribers_for_site(site_id, self.db_path)
            for sub in subscribers:
                if not alert_allowed(sub.get("sensitivity"), item["type"], severity):
                    continue
                adapter = self.adapters.get(sub["channel"])
                if adapter is None:
                    continue
                # ... existing send / log_notification code unchanged ...
```

- [ ] **Step 8: Run to verify pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_dispatcher.py tests/monitor/test_sensitivity.py tests/db -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add monitor/scheduler.py monitor/dispatcher.py db/models.py tests/monitor/test_sensitivity.py tests/monitor/test_dispatcher.py
git commit -m "feat(routing): per-page sensitivity dial and status-aware alert routing"
```

---

### Task 3: Pin-page database helpers

**Files:**
- Modify: `db/models.py` (append after `get_page_by_edit_token`)
- Test: `tests/db/test_pin_pages.py`

**Interfaces:**
- Produces, all in `db/models.py`:
  - `create_pin_page(owner_chat_id, db_path=None) -> dict` — inserts a `user_pages` row with `page_name='My river'`, `status='pending'`, `owner_chat_id` (may be `None`), returns the full row dict (includes `id`, `edit_token`, `public_token`).
  - `get_page_for_chat(chat_id, db_path=None) -> dict | None` — the chat's page whose `status != 'stopped'`, newest first, or None.
  - `bind_page_to_chat(edit_token, chat_id, display_name, db_path=None) -> dict | None` — sets `owner_chat_id`, adds the chat as an active telegram `page_subscribers` row, promotes `status` from `pending` to `active` if the page already has a pin; returns the updated row or None if the token is unknown, the page is stopped, or it already belongs to a different chat.
  - `save_pin(page_id, lat, lon, river_name, sensitivity, usgs_sites, noaa_gauges, db_path=None) -> None` — one transaction: stores pin fields, replaces `page_sites`/`page_noaa_gauges` links, upserts each source with `origin='user'` and `active=1`, sets `status='active'` when `owner_chat_id` is set else leaves `'pending'`. `usgs_sites` is `[{"site_number", "station_name", "parameter_code"}]` (already validated by the caller); `noaa_gauges` is `[{"lid", "station_name", "action_stage", "minor_flood_stage", "moderate_flood_stage", "major_flood_stage"}]`.
  - `set_page_sensitivity(page_id, sensitivity, db_path=None) -> None` — raises `ValueError` for a value outside `('floods', 'unusual', 'all')`.
  - `set_page_status(page_id, status, db_path=None) -> None` — raises `ValueError` outside `('pending', 'active', 'paused', 'stopped')`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/db/test_pin_pages.py
import pytest

from db.models import (
    bind_page_to_chat, create_pin_page, get_active_page_subscribers, get_db,
    get_page_by_edit_token, get_page_for_chat, get_page_gauges, get_page_sites,
    save_pin, set_page_sensitivity, set_page_status,
)

USGS = [{"site_number": "03294500", "station_name": "OHIO RIVER AT LOUISVILLE",
         "parameter_code": "00065"}]
NOAA = [{"lid": "MLUK2", "station_name": "McAlpine Upper", "action_stage": 21.0,
         "minor_flood_stage": 23.0, "moderate_flood_stage": 30.0,
         "major_flood_stage": 38.0}]


def _row(tmp_db, table, **where):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    clause = " AND ".join(f"{k}=%s" for k in where)
    cur.execute(f"SELECT * FROM {table} WHERE {clause}", tuple(where.values()))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def test_create_pin_page_is_pending_and_owned(tmp_db):
    page = create_pin_page(123, tmp_db)
    assert page["status"] == "pending"
    assert page["owner_chat_id"] == 123
    assert page["sensitivity"] == "unusual"
    assert page["edit_token"] and page["public_token"]


def test_create_pin_page_without_owner(tmp_db):
    page = create_pin_page(None, tmp_db)
    assert page["owner_chat_id"] is None


def test_get_page_for_chat_ignores_stopped_pages(tmp_db):
    old = create_pin_page(5, tmp_db)
    set_page_status(old["id"], "stopped", tmp_db)
    assert get_page_for_chat(5, tmp_db) is None
    new = create_pin_page(5, tmp_db)
    assert get_page_for_chat(5, tmp_db)["id"] == new["id"]


def test_bind_page_to_chat_sets_owner_and_subscribes(tmp_db):
    page = create_pin_page(None, tmp_db)
    bound = bind_page_to_chat(page["edit_token"], 42, "Ann", tmp_db)
    assert bound["owner_chat_id"] == 42
    assert bound["status"] == "pending"          # no pin yet
    subs = get_active_page_subscribers(page["id"], tmp_db)
    assert [(s["channel"], s["channel_id"]) for s in subs] == [("telegram", "42")]


def test_bind_activates_a_page_that_already_has_a_pin(tmp_db):
    page = create_pin_page(None, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["status"] == "pending"
    bound = bind_page_to_chat(page["edit_token"], 42, "Ann", tmp_db)
    assert bound["status"] == "active"


def test_bind_refuses_unknown_token_and_foreign_page(tmp_db):
    assert bind_page_to_chat("nope", 42, "Ann", tmp_db) is None
    page = create_pin_page(1, tmp_db)
    assert bind_page_to_chat(page["edit_token"], 2, "Bob", tmp_db) is None
    assert bind_page_to_chat(page["edit_token"], 1, "Ann", tmp_db)["owner_chat_id"] == 1


def test_save_pin_provisions_sources_with_user_origin(tmp_db):
    page = create_pin_page(7, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "floods", USGS, NOAA, tmp_db)
    row = get_page_by_edit_token(page["edit_token"], tmp_db)
    assert (row["pin_lat"], row["pin_lon"]) == (38.25, -85.75)
    assert row["river_name"] == "Ohio River"
    assert row["sensitivity"] == "floods"
    assert row["status"] == "active"
    site = _row(tmp_db, "sites", site_number="03294500")
    assert site["origin"] == "user" and site["active"] == 1
    assert site["parameter_code"] == "00065"
    gauge = _row(tmp_db, "noaa_gauges", lid="MLUK2")
    assert gauge["origin"] == "user" and gauge["active"] == 1
    assert [s["id"] for s in get_page_sites(page["id"], tmp_db)] == [site["id"]]
    assert [g["id"] for g in get_page_gauges(page["id"], tmp_db)] == [gauge["id"]]


def test_save_pin_replaces_previous_links_and_reactivates(tmp_db):
    page = create_pin_page(7, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, NOAA, tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("UPDATE sites SET active=0")
    conn.commit()
    cur.close()
    conn.close()
    other = [{"site_number": "03293551", "station_name": "OHIO R US OF MCALPINE",
              "parameter_code": "00060"}]
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS + other, [], tmp_db)
    numbers = sorted(s["site_number"] for s in get_page_sites(page["id"], tmp_db))
    assert numbers == ["03293551", "03294500"]
    assert get_page_gauges(page["id"], tmp_db) == []
    assert _row(tmp_db, "sites", site_number="03294500")["active"] == 1


def test_save_pin_keeps_admin_origin_on_existing_site(tmp_db):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('03294500', 'Admin')")
    conn.commit()
    cur.close()
    conn.close()
    page = create_pin_page(7, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    assert _row(tmp_db, "sites", site_number="03294500")["origin"] == "admin"


def test_save_pin_stays_pending_without_owner(tmp_db):
    page = create_pin_page(None, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["status"] == "pending"


def test_set_sensitivity_and_status_validate(tmp_db):
    page = create_pin_page(7, tmp_db)
    set_page_sensitivity(page["id"], "all", tmp_db)
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["sensitivity"] == "all"
    with pytest.raises(ValueError):
        set_page_sensitivity(page["id"], "loud", tmp_db)
    set_page_status(page["id"], "paused", tmp_db)
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["status"] == "paused"
    with pytest.raises(ValueError):
        set_page_status(page["id"], "asleep", tmp_db)
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/db/test_pin_pages.py -v`
Expected: FAIL — `ImportError: cannot import name 'bind_page_to_chat'`.

- [ ] **Step 3: Implement the helpers**

Append to `db/models.py` after `get_page_by_edit_token`:

```python
SENSITIVITY_LEVELS = ("floods", "unusual", "all")
PAGE_STATUSES = ("pending", "active", "paused", "stopped")
PIN_PAGE_NAME = "My river"


def create_pin_page(owner_chat_id, db_path=None):
    """Create a pending pin page, optionally owned by a Telegram chat; return its row."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO user_pages
               (public_token, edit_token, page_name, owner_chat_id, status)
               VALUES (%s, %s, %s, %s, 'pending') RETURNING *""",
            (str(uuid.uuid4()), str(uuid.uuid4()), PIN_PAGE_NAME, owner_chat_id)
        )
        row = cur.fetchone()
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return dict(row)


def get_page_for_chat(chat_id, db_path=None):
    """Return the newest non-stopped page owned by `chat_id`, or None."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT * FROM user_pages
               WHERE owner_chat_id=%s AND status <> 'stopped'
               ORDER BY id DESC LIMIT 1""",
            (int(chat_id),)
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return dict(row) if row else None


def bind_page_to_chat(edit_token, chat_id, display_name, db_path=None):
    """Make `chat_id` the owner and an active subscriber of the page.

    Returns the updated row, or None when the token is unknown, the page is
    stopped, or another chat already owns it. A page that already has its
    pin becomes active; one still waiting for a pin stays pending.
    """
    chat_id = int(chat_id)
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM user_pages WHERE edit_token=%s", (edit_token,))
        page = cur.fetchone()
        if page is None or page["status"] == "stopped":
            return None
        if page["owner_chat_id"] is not None and page["owner_chat_id"] != chat_id:
            return None
        new_status = "active" if page["pin_lat"] is not None else page["status"]
        cur.execute(
            "UPDATE user_pages SET owner_chat_id=%s, status=%s WHERE id=%s RETURNING *",
            (chat_id, new_status, page["id"])
        )
        updated = cur.fetchone()
        cur.execute(
            """INSERT INTO page_subscribers
               (page_id, channel, channel_id, display_name, status)
               VALUES (%s, 'telegram', %s, %s, 'active')
               ON CONFLICT (page_id, channel, channel_id)
               DO UPDATE SET display_name = EXCLUDED.display_name, status = 'active'""",
            (page["id"], str(chat_id), display_name or "Telegram User")
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return dict(updated)


def save_pin(page_id, lat, lon, river_name, sensitivity, usgs_sites, noaa_gauges,
             db_path=None):
    """Store a page's pin and replace its sources, provisioning any new ones.

    Everything happens in one transaction so a failure part-way leaves the
    page as it was. Sources are inserted with origin='user'; an existing row
    keeps its origin but is reactivated, so a gauge the retirement sweep
    switched off comes back the moment someone selects it again.
    """
    if sensitivity not in SENSITIVITY_LEVELS:
        raise ValueError(f"unknown sensitivity {sensitivity!r}")
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE user_pages
               SET pin_lat=%s, pin_lon=%s, river_name=%s, sensitivity=%s,
                   status = CASE WHEN owner_chat_id IS NULL THEN 'pending' ELSE 'active' END
               WHERE id=%s""",
            (lat, lon, river_name, sensitivity, page_id)
        )
        cur.execute("DELETE FROM page_sites WHERE page_id=%s", (page_id,))
        cur.execute("DELETE FROM page_noaa_gauges WHERE page_id=%s", (page_id,))
        for site in usgs_sites:
            cur.execute(
                """INSERT INTO sites (site_number, station_name, parameter_code, origin, active)
                   VALUES (%s, %s, %s, 'user', 1)
                   ON CONFLICT (site_number) DO UPDATE SET active = 1
                   RETURNING id""",
                (site["site_number"], site.get("station_name", ""),
                 site.get("parameter_code", "00065"))
            )
            site_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO page_sites (page_id, site_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (page_id, site_id)
            )
        for gauge in noaa_gauges:
            cur.execute(
                """INSERT INTO noaa_gauges
                   (lid, station_name, action_stage, minor_flood_stage,
                    moderate_flood_stage, major_flood_stage, origin, active)
                   VALUES (%s, %s, %s, %s, %s, %s, 'user', 1)
                   ON CONFLICT (lid) DO UPDATE SET active = 1
                   RETURNING id""",
                (gauge["lid"], gauge.get("station_name", ""),
                 gauge.get("action_stage"), gauge.get("minor_flood_stage"),
                 gauge.get("moderate_flood_stage"), gauge.get("major_flood_stage"))
            )
            gauge_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO page_noaa_gauges (page_id, noaa_gauge_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                (page_id, gauge_id)
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def set_page_sensitivity(page_id, sensitivity, db_path=None):
    """Set the page's sensitivity dial; ValueError on an unknown level."""
    if sensitivity not in SENSITIVITY_LEVELS:
        raise ValueError(f"unknown sensitivity {sensitivity!r}")
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("UPDATE user_pages SET sensitivity=%s WHERE id=%s", (sensitivity, page_id))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def set_page_status(page_id, status, db_path=None):
    """Set the page's lifecycle status; ValueError on an unknown status."""
    if status not in PAGE_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("UPDATE user_pages SET status=%s WHERE id=%s", (status, page_id))
        conn.commit()
    finally:
        cur.close()
        conn.close()
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/db -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add db/models.py tests/db/test_pin_pages.py
git commit -m "feat(db): pin-page helpers — create, bind to chat, save pin, dial and status"
```

---

### Task 4: Retirement sweep

**Files:**
- Create: `monitor/retirement.py`
- Modify: `monitor/scheduler.py` (`SchedulerThread.run` calls the sweep hourly)
- Test: `tests/db/test_retirement.py`

**Interfaces:**
- Consumes: `create_pin_page`, `save_pin`, `set_page_status` from Task 3.
- Produces: `sweep(db_path=None) -> dict` with keys `sites`, `gauges`, `pending_pages` (counts affected). `SchedulerThread.SWEEP_INTERVAL_SECONDS = 3600`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/db/test_retirement.py
from db.models import create_pin_page, get_db, save_pin, set_page_status
from monitor.retirement import sweep

USGS = [{"site_number": "03294500", "station_name": "Ohio", "parameter_code": "00065"}]
NOAA = [{"lid": "MLUK2", "station_name": "McAlpine", "action_stage": 21.0,
         "minor_flood_stage": None, "moderate_flood_stage": None,
         "major_flood_stage": None}]


def _active(tmp_db, table, key, value):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(f"SELECT active FROM {table} WHERE {key}=%s", (value,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["active"] if row else None


def _exec(tmp_db, sql, params=()):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    cur.close()
    conn.close()


def test_user_sources_with_no_live_page_are_deactivated(tmp_db):
    page = create_pin_page(1, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio", "unusual", USGS, NOAA, tmp_db)
    set_page_status(page["id"], "stopped", tmp_db)
    result = sweep(tmp_db)
    assert result["sites"] == 1 and result["gauges"] == 1
    assert _active(tmp_db, "sites", "site_number", "03294500") == 0
    assert _active(tmp_db, "noaa_gauges", "lid", "MLUK2") == 0


def test_paused_page_keeps_its_sources(tmp_db):
    page = create_pin_page(1, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio", "unusual", USGS, NOAA, tmp_db)
    set_page_status(page["id"], "paused", tmp_db)
    sweep(tmp_db)
    assert _active(tmp_db, "sites", "site_number", "03294500") == 1
    assert _active(tmp_db, "noaa_gauges", "lid", "MLUK2") == 1


def test_admin_sources_are_never_touched(tmp_db):
    _exec(tmp_db, "INSERT INTO sites (site_number, station_name) VALUES ('11111111', 'Admin')")
    _exec(tmp_db, "INSERT INTO noaa_gauges (lid, station_name) VALUES ('ADMN1', 'Admin')")
    result = sweep(tmp_db)
    assert result == {"sites": 0, "gauges": 0, "pending_pages": 0}
    assert _active(tmp_db, "sites", "site_number", "11111111") == 1
    assert _active(tmp_db, "noaa_gauges", "lid", "ADMN1") == 1


def test_source_shared_with_a_live_page_survives(tmp_db):
    a = create_pin_page(1, tmp_db)
    b = create_pin_page(2, tmp_db)
    save_pin(a["id"], 38.0, -85.0, "Ohio", "unusual", USGS, [], tmp_db)
    save_pin(b["id"], 38.0, -85.0, "Ohio", "unusual", USGS, [], tmp_db)
    set_page_status(a["id"], "stopped", tmp_db)
    sweep(tmp_db)
    assert _active(tmp_db, "sites", "site_number", "03294500") == 1


def test_stale_pending_page_is_deleted_but_fresh_one_kept(tmp_db):
    stale = create_pin_page(1, tmp_db)
    fresh = create_pin_page(2, tmp_db)
    _exec(tmp_db,
          "UPDATE user_pages SET created_at=(NOW() - INTERVAL '25 hours')::TEXT WHERE id=%s",
          (stale["id"],))
    result = sweep(tmp_db)
    assert result["pending_pages"] == 1
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT id FROM user_pages ORDER BY id")
    ids = [r["id"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    assert ids == [fresh["id"]]


def test_stale_pending_page_with_a_pin_is_kept(tmp_db):
    page = create_pin_page(None, tmp_db)          # web-first, never bound
    save_pin(page["id"], 38.0, -85.0, "Ohio", "unusual", USGS, [], tmp_db)
    _exec(tmp_db,
          "UPDATE user_pages SET created_at=(NOW() - INTERVAL '25 hours')::TEXT WHERE id=%s",
          (page["id"],))
    assert sweep(tmp_db)["pending_pages"] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/db/test_retirement.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitor.retirement'`.

- [ ] **Step 3: Implement the sweep**

```python
# monitor/retirement.py
"""Retire user-provisioned sources nobody references any more.

A pin page provisions USGS sites and NOAA gauges automatically (origin =
'user'). When every page that referenced one has been stopped, polling it
would be wasted API calls, so this sweep switches it off. Rows are never
deleted: history and conditions stay, and re-selecting the source flips it
back on. Admin-origin rows are never touched.

Pending pages that never received a pin are deleted after 24 hours so an
abandoned /start does not accumulate rows forever.
"""

import logging

from db.models import get_db

logger = logging.getLogger(__name__)

PENDING_PAGE_MAX_AGE_HOURS = 24

_LIVE_STATUSES = ("active", "paused")


def sweep(db_path=None):
    """Deactivate unreferenced user sources and drop stale pending pages.

    Returns {"sites": n, "gauges": n, "pending_pages": n}.
    """
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE sites s SET active = 0
               WHERE s.origin = 'user' AND s.active = 1
                 AND NOT EXISTS (
                     SELECT 1 FROM page_sites ps
                     JOIN user_pages up ON up.id = ps.page_id
                     WHERE ps.site_id = s.id AND up.status IN %s)""",
            (_LIVE_STATUSES,)
        )
        sites = cur.rowcount
        cur.execute(
            """UPDATE noaa_gauges g SET active = 0
               WHERE g.origin = 'user' AND g.active = 1
                 AND NOT EXISTS (
                     SELECT 1 FROM page_noaa_gauges png
                     JOIN user_pages up ON up.id = png.page_id
                     WHERE png.noaa_gauge_id = g.id AND up.status IN %s)""",
            (_LIVE_STATUSES,)
        )
        gauges = cur.rowcount
        cur.execute(
            """SELECT id FROM user_pages
               WHERE status = 'pending' AND pin_lat IS NULL
                 AND created_at::timestamptz < NOW() - (%s * INTERVAL '1 hour')""",
            (PENDING_PAGE_MAX_AGE_HOURS,)
        )
        stale_ids = [r["id"] for r in cur.fetchall()]
        if stale_ids:
            cur.execute("DELETE FROM page_subscribers WHERE page_id = ANY(%s)", (stale_ids,))
            cur.execute("DELETE FROM page_sites WHERE page_id = ANY(%s)", (stale_ids,))
            cur.execute("DELETE FROM page_noaa_gauges WHERE page_id = ANY(%s)", (stale_ids,))
            cur.execute("DELETE FROM user_pages WHERE id = ANY(%s)", (stale_ids,))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    result = {"sites": sites, "gauges": gauges, "pending_pages": len(stale_ids)}
    if any(result.values()):
        logger.info("Retirement sweep: %s", result)
    return result
```

In `monitor/scheduler.py`, add `from monitor.retirement import sweep` and change `SchedulerThread`:

```python
    CHECK_INTERVAL_SECONDS = 300  # check every 5 minutes
    SWEEP_INTERVAL_SECONDS = 3600  # retire unreferenced user sources hourly

    def __init__(self, notification_queue, db_path=None, stop_event=None):
        """Store the notification queue, optional db_path, and stop event."""
        super().__init__(name="SchedulerThread", daemon=True)
        self.notification_queue = notification_queue
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()
        self._last_sweep = 0.0

    def run(self):
        """Check reminders each iteration, sweep hourly, until stop_event is set."""
        logger.info("SchedulerThread started")
        while not self.stop_event.is_set():
            self._check_reminders()
            self._maybe_sweep()
            self.stop_event.wait(timeout=self.CHECK_INTERVAL_SECONDS)
        logger.info("SchedulerThread stopped")

    def _maybe_sweep(self):
        """Run the retirement sweep if SWEEP_INTERVAL_SECONDS have passed."""
        import time
        now = time.monotonic()
        if now - self._last_sweep < self.SWEEP_INTERVAL_SECONDS:
            return
        self._last_sweep = now
        try:
            sweep(self.db_path)
        except Exception:
            logger.exception("Retirement sweep failed")
```

(Put `import time` at the top of the module instead of inside the method.)

- [ ] **Step 4: Run to verify pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/db/test_retirement.py tests/monitor/test_scheduler.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add monitor/retirement.py monitor/scheduler.py tests/db/test_retirement.py
git commit -m "feat(monitor): hourly sweep retires unreferenced user sources and stale pending pages"
```

---

### Task 5: NWPS gauges near a point

**Files:**
- Modify: `monitor/noaa_client.py` (add `gauges_near`)
- Test: `tests/monitor/test_noaa_client.py`

**Interfaces:**
- Produces: `gauges_near(lat, lon, radius_miles, timeout=TIMEOUT) -> list[dict]` with keys `lid`, `name`, `usgs_id` (str or None), `lat`, `lon`. Returns `[]` on any failure. Never raises.

Before coding, confirm the live response shape with one request (no auth needed):

```bash
curl -s "https://api.water.noaa.gov/nwps/v1/gauges?bbox.xmin=-85.9&bbox.ymin=38.1&bbox.xmax=-85.6&bbox.ymax=38.4&srid=EPSG_4326"
```

Expected: `{"gauges": [{"lid": "MLUK2", "usgsId": "03293551", "name": "...", "latitude": 38.28, "longitude": -85.76, ...}, ...]}`. If the field names differ, adjust `_gauge_from_payload` below and the fixtures to match; the parsing is deliberately tolerant of `latitude`/`lat` and `longitude`/`lon`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/monitor/test_noaa_client.py`:

```python
from monitor.noaa_client import gauges_near


def _mock_gauges_response(payload, status=200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = payload
    return mock


def test_gauges_near_parses_listing_and_bbox():
    payload = {"gauges": [
        {"lid": "MLUK2", "usgsId": "03293551", "name": "McAlpine Upper",
         "latitude": 38.28, "longitude": -85.76},
        {"lid": "NOUS1", "usgsId": None, "name": "No USGS",
         "latitude": 38.30, "longitude": -85.70},
    ]}
    with patch("monitor.noaa_client.requests.get",
               return_value=_mock_gauges_response(payload)) as get:
        rows = gauges_near(38.25, -85.75, 10)
    assert [r["lid"] for r in rows] == ["MLUK2", "NOUS1"]
    assert rows[0]["usgs_id"] == "03293551" and rows[1]["usgs_id"] is None
    assert rows[0]["lat"] == 38.28 and rows[0]["lon"] == -85.76
    params = get.call_args.kwargs["params"]
    assert params["bbox.ymin"] < 38.25 < params["bbox.ymax"]
    assert params["bbox.xmin"] < -85.75 < params["bbox.xmax"]


def test_gauges_near_returns_empty_on_http_error():
    with patch("monitor.noaa_client.requests.get",
               return_value=_mock_gauges_response({}, status=500)):
        assert gauges_near(38.25, -85.75, 10) == []


def test_gauges_near_returns_empty_on_exception():
    with patch("monitor.noaa_client.requests.get", side_effect=Exception("boom")):
        assert gauges_near(38.25, -85.75, 10) == []


def test_gauges_near_skips_entries_without_lid_or_coordinates():
    payload = {"gauges": [{"name": "nameless"},
                          {"lid": "NOLL1", "name": "no coords"}]}
    with patch("monitor.noaa_client.requests.get",
               return_value=_mock_gauges_response(payload)):
        assert gauges_near(38.25, -85.75, 10) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_noaa_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'gauges_near'`.

- [ ] **Step 3: Implement**

Add to `monitor/noaa_client.py` after `fetch_current_stage`:

```python
import math  # at top of module

_MILES_PER_DEGREE_LAT = 69.0


def _gauge_from_payload(item):
    """Normalise one NWPS gauge listing entry, or None if unusable."""
    if not isinstance(item, dict) or not item.get("lid"):
        return None
    lat = item.get("latitude", item.get("lat"))
    lon = item.get("longitude", item.get("lon"))
    if lat is None or lon is None:
        return None
    usgs_id = item.get("usgsId") or None
    return {
        "lid": str(item["lid"]).upper(),
        "name": item.get("name") or item["lid"],
        "usgs_id": str(usgs_id) if usgs_id else None,
        "lat": float(lat),
        "lon": float(lon),
    }


def gauges_near(lat, lon, radius_miles, timeout=TIMEOUT):
    """List NWPS gauges inside a bounding box of `radius_miles` around a point.

    Returns [{lid, name, usgs_id, lat, lon}] — an empty list on any failure,
    because a discovery step that cannot list NOAA gauges should degrade to
    "no NOAA gauges proposed", not abort the whole pin flow.
    """
    dlat = radius_miles / _MILES_PER_DEGREE_LAT
    dlon = radius_miles / (_MILES_PER_DEGREE_LAT * max(math.cos(math.radians(lat)), 0.01))
    params = {
        "bbox.xmin": lon - dlon, "bbox.ymin": lat - dlat,
        "bbox.xmax": lon + dlon, "bbox.ymax": lat + dlat,
        "srid": "EPSG_4326",
    }
    try:
        resp = requests.get(f"{NWPS_BASE}/gauges", params=params, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("NWPS gauge listing failed: HTTP %s", resp.status_code)
            return []
        data = resp.json()
    except Exception:
        logger.exception("Error listing NWPS gauges near %s,%s", lat, lon)
        return []
    items = data.get("gauges", []) if isinstance(data, dict) else []
    rows = [_gauge_from_payload(item) for item in items]
    return [r for r in rows if r]
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_noaa_client.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add monitor/noaa_client.py tests/monitor/test_noaa_client.py
git commit -m "feat(noaa): list NWPS gauges inside a bounding box"
```

---

### Task 6: Pin discovery module

**Files:**
- Create: `monitor/pin_discovery.py`
- Create: `tests/monitor/test_pin_discovery.py`

**Interfaces:**
- Consumes: `gauges_near` from Task 5.
- Produces:
  ```python
  @dataclass
  class Candidate:
      kind: str            # "usgs" | "noaa"
      id: str              # USGS site number (digits only) or NOAA LID
      name: str
      distance_km: float
      tag: str             # "upstream" | "downstream" | "nearby" | "nearest"
      usgs_id: str | None = None      # NOAA only: the USGS number NWPS links to
      parameter_code: str | None = None  # USGS only: "00065" preferred, else "00060"

  @dataclass
  class Discovery:
      river_name: str | None
      snap: str            # "on_network" | "off_network" | "failed"
      candidates: list[Candidate]

  def discover(lat, lon, *, reach_km, fallback_radius_miles) -> Discovery
  def haversine_km(lat1, lon1, lat2, lon2) -> float
  ```
  `Discovery.to_dict()` returns a JSON-ready dict (`{"river_name", "snap", "candidates": [asdict(...)]}`).

NLDI endpoints used (no key; verify with one request each before coding):

```bash
curl -s "https://api.water.usgs.gov/nldi/linked-data/comid/position?coords=POINT(-85.76%2038.28)"
curl -s "https://api.water.usgs.gov/nldi/linked-data/comid/<comid from above>/navigation/UM/nwissite?distance=50"
```

Position returns a GeoJSON FeatureCollection whose first feature has `geometry` (LineString) and `properties.comid`, `properties.name` (GNIS river name, may be empty). Navigation returns a FeatureCollection of Point features with `properties.identifier` (`"USGS-03293551"`), `properties.name`, and `geometry.coordinates` `[lon, lat]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/monitor/test_pin_discovery.py
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from monitor import pin_discovery
from monitor.pin_discovery import Discovery, discover, haversine_km

PIN = (38.28, -85.76)  # Ohio River at Louisville

POSITION = {"type": "FeatureCollection", "features": [{
    "type": "Feature",
    "geometry": {"type": "LineString",
                 "coordinates": [[-85.77, 38.279], [-85.75, 38.281]]},
    "properties": {"comid": "1234567", "name": "Ohio River"},
}]}

FAR_POSITION = {"type": "FeatureCollection", "features": [{
    "type": "Feature",
    "geometry": {"type": "LineString",
                 "coordinates": [[-85.76, 38.40], [-85.75, 38.41]]},   # ~13 km north
    "properties": {"comid": "999", "name": "Some Creek"},
}]}


def _site(number, name, lon, lat):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"identifier": f"USGS-{number}", "name": name}}


UM = {"type": "FeatureCollection", "features": [
    _site("03293551", "OHIO R US OF MCALPINE", -85.74, 38.29)]}
DM = {"type": "FeatureCollection", "features": [
    _site("03294500", "OHIO RIVER AT LOUISVILLE", -85.80, 38.27),
    _site("03294600", "OHIO R AT CANE RUN", -85.90, 38.25)]}
EMPTY = {"type": "FeatureCollection", "features": []}


def _resp(payload, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    return m


def _nldi(position=POSITION, um=UM, dm=DM):
    """Return a requests.get side effect answering the three NLDI URLs."""
    def side_effect(url, **kwargs):
        if "/comid/position" in url:
            return _resp(position)
        if "/navigation/UM/" in url:
            return _resp(um)
        if "/navigation/DM/" in url:
            return _resp(dm)
        raise AssertionError(f"unexpected URL {url}")
    return side_effect


def _catalog(*rows):
    """seriesCatalogOutput frame: rows of (site_no, parm_cd)."""
    return pd.DataFrame(rows, columns=["site_no", "parm_cd"]), None


ALL_HAVE_STAGE = _catalog(("03293551", "00065"), ("03294500", "00065"),
                          ("03294500", "00060"), ("03294600", "00060"))


@pytest.fixture
def nwps():
    with patch("monitor.pin_discovery.gauges_near") as g:
        g.return_value = [
            {"lid": "MLUK2", "name": "McAlpine Upper", "usgs_id": "03293551",
             "lat": 38.29, "lon": -85.74},
            {"lid": "XXXX1", "name": "Some Other Creek", "usgs_id": None,
             "lat": 38.30, "lon": -85.60},
        ]
        yield g


def test_haversine_known_distance():
    assert haversine_km(38.0, -85.0, 39.0, -85.0) == pytest.approx(111.2, abs=0.5)


def test_on_network_pin_proposes_stem_gauges_in_order(nwps):
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi()), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert isinstance(result, Discovery)
    assert result.snap == "on_network"
    assert result.river_name == "Ohio River"
    usgs = [c for c in result.candidates if c.kind == "usgs"]
    assert [(c.id, c.tag) for c in usgs] == [
        ("03293551", "upstream"), ("03294500", "downstream"), ("03294600", "downstream")]
    assert usgs[0].parameter_code == "00065"
    assert usgs[2].parameter_code == "00060"          # only discharge available
    assert usgs[1].distance_km < usgs[2].distance_km  # downstream sorted by distance


def test_noaa_gauge_matching_a_stem_site_inherits_its_tag(nwps):
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi()), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    noaa = {c.id: c for c in result.candidates if c.kind == "noaa"}
    assert noaa["MLUK2"].tag == "upstream" and noaa["MLUK2"].usgs_id == "03293551"
    assert noaa["XXXX1"].tag == "nearby"


def test_sites_without_stage_or_discharge_are_dropped(nwps):
    only_temp = _catalog(("03293551", "00010"), ("03294500", "00065"))
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi()), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=only_temp):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert [c.id for c in result.candidates if c.kind == "usgs"] == ["03294500"]


def test_off_network_pin_falls_back_to_bbox(nwps):
    what_sites = pd.DataFrame([
        {"site_no": "03294500", "station_nm": "OHIO RIVER AT LOUISVILLE",
         "dec_lat_va": 38.27, "dec_long_va": -85.80},
    ]), None
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi(position=FAR_POSITION)), \
         patch("monitor.pin_discovery.nwis.what_sites", return_value=what_sites), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert result.snap == "off_network"
    assert result.river_name is None
    usgs = [c for c in result.candidates if c.kind == "usgs"]
    assert [(c.id, c.tag) for c in usgs] == [("03294500", "nearest")]


def test_empty_navigation_falls_back_to_bbox(nwps):
    what_sites = pd.DataFrame([
        {"site_no": "03294500", "station_nm": "OHIO RIVER AT LOUISVILLE",
         "dec_lat_va": 38.27, "dec_long_va": -85.80},
    ]), None
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi(um=EMPTY, dm=EMPTY)), \
         patch("monitor.pin_discovery.nwis.what_sites", return_value=what_sites), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert result.snap == "on_network"
    assert result.river_name == "Ohio River"
    assert [c.tag for c in result.candidates if c.kind == "usgs"] == ["nearest"]


def test_nldi_error_falls_back_and_reports_failed_snap(nwps):
    what_sites = pd.DataFrame([
        {"site_no": "03294500", "station_nm": "OHIO RIVER AT LOUISVILLE",
         "dec_lat_va": 38.27, "dec_long_va": -85.80},
    ]), None
    with patch("monitor.pin_discovery.requests.get", side_effect=Exception("down")), \
         patch("monitor.pin_discovery.nwis.what_sites", return_value=what_sites), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert result.snap == "failed"
    assert [c.id for c in result.candidates if c.kind == "usgs"] == ["03294500"]


def test_everything_failing_yields_empty_but_valid_result():
    with patch("monitor.pin_discovery.requests.get", side_effect=Exception("down")), \
         patch("monitor.pin_discovery.nwis.what_sites", side_effect=Exception("down")), \
         patch("monitor.pin_discovery.nwis.get_info", side_effect=Exception("down")), \
         patch("monitor.pin_discovery.gauges_near", return_value=[]):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert result.snap == "failed"
    assert result.candidates == []
    assert result.to_dict() == {"river_name": None, "snap": "failed", "candidates": []}


def test_to_dict_is_json_ready(nwps):
    import json
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi()), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    text = json.dumps(result.to_dict())
    assert '"kind": "usgs"' in text and '"tag": "upstream"' in text
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_pin_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitor.pin_discovery'`.

- [ ] **Step 3: Implement**

```python
# monitor/pin_discovery.py
"""Turn a dropped pin into a list of gauges on that stretch of river.

The USGS Network Linked Data Index (NLDI) snaps a point to the nearest
NHDPlus flowline and can walk the main stem upstream (UM) and downstream
(DM), returning the USGS gauges on it. That is hydrologically right — it
follows the channel and excludes tributaries — so it is tried first. When
the pin is off the network, navigation finds nothing, or NLDI is down, the
existing bounding-box site search fills the list instead, tagged "nearest",
so the confirm screen is never empty for a bad reason.

NOAA gauges have no NLDI layer; they come from the NWPS bounding-box listing
and are matched to proposed USGS sites by the USGS id NWPS publishes.

This module touches no database. Every network step degrades to "nothing
from that step" and logs why, never raising to the caller.
"""

import logging
import math
from dataclasses import asdict, dataclass, field

import dataretrieval.nwis as nwis
import requests

from monitor.noaa_client import gauges_near

logger = logging.getLogger(__name__)

NLDI_BASE = "https://api.water.usgs.gov/nldi/linked-data"
TIMEOUT = 15
#: A pin farther than this from the snapped flowline is treated as off-network.
SNAP_MAX_KM = 2.0
#: Parameters a site must report to be worth proposing; first match wins.
WANTED_PARAMETERS = ("00065", "00060")


@dataclass
class Candidate:
    kind: str
    id: str
    name: str
    distance_km: float
    tag: str
    usgs_id: str | None = None
    parameter_code: str | None = None


@dataclass
class Discovery:
    river_name: str | None
    snap: str
    candidates: list = field(default_factory=list)

    def to_dict(self):
        """JSON-ready form for the /discover route."""
        return {
            "river_name": self.river_name,
            "snap": self.snap,
            "candidates": [asdict(c) for c in self.candidates],
        }


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ── NLDI ────────────────────────────────────────────────────────────────────

def _get_json(url, params=None):
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from {url}")
    return resp.json()


def _snap(lat, lon):
    """Return (comid, river_name, distance_km_to_flowline) or raise."""
    data = _get_json(f"{NLDI_BASE}/comid/position?coords=POINT({lon} {lat})")
    features = data.get("features") or []
    if not features:
        raise RuntimeError("NLDI position returned no flowline")
    feat = features[0]
    props = feat.get("properties") or {}
    comid = str(props.get("comid") or props.get("identifier") or "")
    if not comid:
        raise RuntimeError("NLDI flowline has no comid")
    name = (props.get("name") or "").strip() or None
    coords = (feat.get("geometry") or {}).get("coordinates") or []
    if coords and isinstance(coords[0], (int, float)):
        coords = [coords]
    distance = min((haversine_km(lat, lon, c[1], c[0]) for c in coords), default=0.0)
    return comid, name, distance


def _navigate(comid, direction, reach_km):
    """Return [(site_number, name, lat, lon)] for USGS sites along `direction`."""
    data = _get_json(f"{NLDI_BASE}/comid/{comid}/navigation/{direction}/nwissite",
                     params={"distance": reach_km})
    out = []
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        ident = str(props.get("identifier") or "")
        number = ident.split("-", 1)[1] if "-" in ident else ident
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if not number or len(coords) < 2:
            continue
        out.append((number, props.get("name") or number, float(coords[1]), float(coords[0])))
    return out


# ── USGS helpers ─────────────────────────────────────────────────────────────

def _parameters_by_site(site_numbers):
    """Map site number -> preferred parameter code, dropping sites with neither."""
    if not site_numbers:
        return {}
    df, _ = nwis.get_info(sites=list(site_numbers), seriesCatalogOutput=True)
    available = {}
    if df is not None and len(df):
        for _, row in df.iterrows():
            available.setdefault(str(row["site_no"]), set()).add(str(row["parm_cd"]))
    chosen = {}
    for number in site_numbers:
        for code in WANTED_PARAMETERS:
            if code in available.get(number, ()):
                chosen[number] = code
                break
    return chosen


def _bbox_sites(lat, lon, radius_miles):
    """Nearest-within-radius fallback via nwis.what_sites."""
    dlat = radius_miles / 69.0
    dlon = radius_miles / (69.0 * max(math.cos(math.radians(lat)), 0.01))
    bbox = [round(lon - dlon, 6), round(lat - dlat, 6), round(lon + dlon, 6), round(lat + dlat, 6)]
    df, _ = nwis.what_sites(bBox=bbox, siteType="ST", siteStatus="active")
    out = []
    if df is None or not len(df):
        return out
    for _, row in df.iterrows():
        try:
            out.append((str(row["site_no"]), str(row.get("station_nm", "") or row["site_no"]),
                        float(row["dec_lat_va"]), float(row["dec_long_va"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ── Entry point ──────────────────────────────────────────────────────────────

def discover(lat, lon, *, reach_km, fallback_radius_miles):
    """Propose gauges for a pin. See the module docstring for the strategy."""
    river_name = None
    snap = "failed"
    raw = []   # (number, name, lat, lon, tag)

    try:
        comid, river_name, snap_distance = _snap(lat, lon)
        if snap_distance > SNAP_MAX_KM:
            snap = "off_network"
            river_name = None
            logger.info("Pin %s,%s is %.1f km from the nearest flowline", lat, lon, snap_distance)
        else:
            snap = "on_network"
            for direction, tag in (("UM", "upstream"), ("DM", "downstream")):
                try:
                    for number, name, slat, slon in _navigate(comid, direction, reach_km):
                        raw.append((number, name, slat, slon, tag))
                except Exception as exc:
                    logger.warning("NLDI %s navigation failed for comid %s: %s", direction, comid, exc)
    except Exception as exc:
        logger.warning("NLDI snap failed for %s,%s: %s", lat, lon, exc)

    if not raw:
        try:
            for number, name, slat, slon in _bbox_sites(lat, lon, fallback_radius_miles):
                raw.append((number, name, slat, slon, "nearest"))
        except Exception as exc:
            logger.warning("Bounding-box site search failed for %s,%s: %s", lat, lon, exc)

    # De-duplicate (a site can appear in both directions at the snap point).
    seen = {}
    for number, name, slat, slon, tag in raw:
        seen.setdefault(number, (name, slat, slon, tag))

    try:
        parameters = _parameters_by_site(list(seen))
    except Exception as exc:
        logger.warning("USGS parameter check failed: %s", exc)
        parameters = {number: WANTED_PARAMETERS[0] for number in seen}

    candidates = []
    for number, (name, slat, slon, tag) in seen.items():
        if number not in parameters:
            continue
        candidates.append(Candidate(
            kind="usgs", id=number, name=name,
            distance_km=round(haversine_km(lat, lon, slat, slon), 1),
            tag=tag, parameter_code=parameters[number]))

    tag_by_usgs = {c.id: c.tag for c in candidates}
    try:
        for g in gauges_near(lat, lon, fallback_radius_miles):
            tag = tag_by_usgs.get(g["usgs_id"], "nearby") if g["usgs_id"] else "nearby"
            candidates.append(Candidate(
                kind="noaa", id=g["lid"], name=g["name"],
                distance_km=round(haversine_km(lat, lon, g["lat"], g["lon"]), 1),
                tag=tag, usgs_id=g["usgs_id"]))
    except Exception as exc:
        logger.warning("NWPS gauge listing failed: %s", exc)

    order = {"upstream": 0, "downstream": 1, "nearby": 2, "nearest": 2}
    candidates.sort(key=lambda c: (order.get(c.tag, 3), c.distance_km, c.kind))
    return Discovery(river_name=river_name, snap=snap, candidates=candidates)
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_pin_discovery.py -v`
Expected: all PASS.

- [ ] **Step 5: Live smoke check (manual, one time)**

Run inside the test container (network is allowed there):
`docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test python -c "from monitor.pin_discovery import discover; print(discover(38.28, -85.76, reach_km=50, fallback_radius_miles=25).to_dict())"`
Expected: `snap == 'on_network'`, `river_name == 'Ohio River'`, at least one `usgs` candidate. If a field name in the real payload differs from the fixtures, fix the parser and the fixtures together and re-run Step 4.

- [ ] **Step 6: Commit**

```bash
git add monitor/pin_discovery.py tests/monitor/test_pin_discovery.py
git commit -m "feat(monitor): pin discovery via NLDI main-stem navigation with bbox fallback"
```

---

### Task 7: In-process rate limiter

**Files:**
- Create: `web/ratelimit.py`
- Create: `tests/web/test_ratelimit.py`

**Interfaces:**
- Produces: `RateLimiter(limit: int, per_seconds: float)` with `allow(key: str, now: float | None = None) -> bool` (sliding window; `now` defaults to `time.monotonic()`, injectable for tests) and `reset()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/web/test_ratelimit.py
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
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_ratelimit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.ratelimit'`.

- [ ] **Step 3: Implement**

```python
# web/ratelimit.py
"""A small in-process sliding-window rate limiter.

Used by the public pin routes, which trigger third-party API calls on a
visitor's click. It is per worker process and per container — good enough
to stop one browser hammering NLDI, not a distributed quota. Keys are
whatever the caller chooses (a page token, a client IP).
"""

import threading
import time
from collections import deque


class RateLimiter:
    """Allow at most `limit` calls per `per_seconds` for each key."""

    def __init__(self, limit, per_seconds):
        self.limit = int(limit)
        self.per_seconds = float(per_seconds)
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key, now=None):
        """Record a hit for `key` and return True if it is within the limit."""
        now = time.monotonic() if now is None else now
        with self._lock:
            window = self._hits.setdefault(key, deque())
            cutoff = now - self.per_seconds
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= self.limit:
                return False
            window.append(now)
            return True

    def reset(self):
        """Forget every recorded hit (tests)."""
        with self._lock:
            self._hits.clear()
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_ratelimit.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/ratelimit.py tests/web/test_ratelimit.py
git commit -m "feat(web): sliding-window rate limiter for public pin routes"
```

---

### Task 8: Pin routes and map page

**Files:**
- Modify: `web/routes.py` (imports; new routes after `page_new`; settings group fields)
- Modify: `web/auth.py` (`PUBLIC_ENDPOINTS`)
- Create: `web/templates/pin.html`
- Create: `tests/web/test_pin.py`

**Interfaces:**
- Consumes: `create_pin_page`, `get_page_by_edit_token`, `save_pin` (Task 3); `discover` (Task 6); `RateLimiter` (Task 7); `validate_usgs_site`, `fetch_gauge_metadata` (existing); queue item `direct` (Task 2).
- Produces routes (all public, token-authenticated):
  - `GET /pin` → creates a pending page with no owner, 302 to `/pin/<edit_token>`. Limited to 10 per IP per day (429 otherwise).
  - `GET /pin/<edit_token>` → renders `pin.html`; 404 unknown token; 410 for a `stopped` page.
  - `POST /pin/<edit_token>/discover` → JSON in `{"lat": float, "lon": float}`; JSON out is `Discovery.to_dict()`. 400 on bad coordinates; 429 over 20/hour/token or 60/hour/IP.
  - `POST /pin/<edit_token>/save` → JSON in `{"lat", "lon", "river_name", "sensitivity", "sources": [{"kind": "usgs", "id": "03294500", "parameter_code": "00065"} | {"kind": "noaa", "id": "MLUK2"}]}`. 400 with `{"error": ...}` on zero sources, bad sensitivity, or bad coordinates. 200 with `{"ok": true, "status": "active"|"pending", "skipped": [...], "bot_link": str|null, "edit_url": str}`. Sources that fail validation are skipped and listed; if *every* source fails, 400.
- Produces module-level limiters in `web/routes.py`: `PIN_CREATE_LIMITER = RateLimiter(10, 86400)`, `DISCOVER_TOKEN_LIMITER = RateLimiter(20, 3600)`, `DISCOVER_IP_LIMITER = RateLimiter(60, 3600)` (tests call `.reset()`).
- Produces helper `bot_deep_link(edit_token, db_path) -> str | None` in `web/routes.py`: `https://t.me/<telegram_bot_username>?start=<edit_token>` or None when the username setting is empty.

- [ ] **Step 1: Write the failing tests**

```python
# tests/web/test_pin.py
import json
from unittest.mock import patch

import pytest

from db.models import (create_pin_page, get_db, get_page_by_edit_token,
                       get_page_gauges, get_page_sites, init_db, set_setting)
from monitor.pin_discovery import Candidate, Discovery
from web import routes as routes_mod


@pytest.fixture
def client(tmp_db, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    init_db(tmp_db)
    routes_mod.PIN_CREATE_LIMITER.reset()
    routes_mod.DISCOVER_TOKEN_LIMITER.reset()
    routes_mod.DISCOVER_IP_LIMITER.reset()
    from web.app import create_app
    import queue
    app = create_app(db_path=tmp_db, notification_queue=queue.Queue())
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c          # deliberately NO admin auth header: these routes are public


DISCOVERY = Discovery(river_name="Ohio River", snap="on_network", candidates=[
    Candidate(kind="usgs", id="03294500", name="OHIO RIVER AT LOUISVILLE",
              distance_km=3.2, tag="downstream", parameter_code="00065"),
    Candidate(kind="noaa", id="MLUK2", name="McAlpine Upper", distance_km=1.1,
              tag="upstream", usgs_id="03293551"),
])

SAVE_BODY = {
    "lat": 38.28, "lon": -85.76, "river_name": "Ohio River", "sensitivity": "floods",
    "sources": [{"kind": "usgs", "id": "03294500", "parameter_code": "00065"},
                {"kind": "noaa", "id": "MLUK2"}],
}


def _valid_site(site_number, parameter_code="00060"):
    return True, "OHIO RIVER AT LOUISVILLE", ""


def _meta(identifier, timeout=10):
    return {"lid": "MLUK2", "station_name": "McAlpine Upper", "usgs_id": "03293551",
            "action_stage": 21.0, "minor_flood_stage": 23.0,
            "moderate_flood_stage": 30.0, "major_flood_stage": 38.0}


def test_get_pin_creates_pending_page_and_redirects(client, tmp_db):
    resp = client.get("/pin")
    assert resp.status_code == 302
    token = resp.headers["Location"].rsplit("/", 1)[-1]
    page = get_page_by_edit_token(token, tmp_db)
    assert page["status"] == "pending" and page["owner_chat_id"] is None


def test_get_pin_is_rate_limited_per_ip(client):
    for _ in range(10):
        assert client.get("/pin").status_code == 302
    assert client.get("/pin").status_code == 429


def test_map_page_renders_without_auth(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    resp = client.get(f"/pin/{page['edit_token']}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "leaflet" in body.lower()
    assert "Find gauges" in body
    assert "Floods only" in body


def test_map_page_404_unknown_and_410_stopped(client, tmp_db):
    assert client.get("/pin/nope").status_code == 404
    page = create_pin_page(1, tmp_db)
    from db.models import set_page_status
    set_page_status(page["id"], "stopped", tmp_db)
    assert client.get(f"/pin/{page['edit_token']}").status_code == 410


def test_discover_returns_candidates_as_json(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    with patch("web.routes.discover", return_value=DISCOVERY) as d:
        resp = client.post(f"/pin/{page['edit_token']}/discover",
                           json={"lat": 38.28, "lon": -85.76})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["river_name"] == "Ohio River"
    assert [c["id"] for c in data["candidates"]] == ["03294500", "MLUK2"]
    kwargs = d.call_args.kwargs
    assert kwargs["reach_km"] == 50.0 and kwargs["fallback_radius_miles"] == 25.0


def test_discover_rejects_bad_coordinates(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    resp = client.post(f"/pin/{page['edit_token']}/discover", json={"lat": 95, "lon": 0})
    assert resp.status_code == 400
    resp = client.post(f"/pin/{page['edit_token']}/discover", json={"lat": "x"})
    assert resp.status_code == 400


def test_discover_rate_limited_per_token(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    with patch("web.routes.discover", return_value=DISCOVERY):
        for _ in range(20):
            assert client.post(f"/pin/{page['edit_token']}/discover",
                               json={"lat": 38.28, "lon": -85.76}).status_code == 200
        resp = client.post(f"/pin/{page['edit_token']}/discover",
                           json={"lat": 38.28, "lon": -85.76})
    assert resp.status_code == 429


def test_save_provisions_sources_and_activates_owned_page(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    set_setting("telegram_bot_username", "RiverBot", tmp_db)
    with patch("web.routes.validate_usgs_site", side_effect=_valid_site), \
         patch("web.routes.fetch_gauge_metadata", side_effect=_meta):
        resp = client.post(f"/pin/{page['edit_token']}/save", json=SAVE_BODY)
    assert resp.status_code == 200, resp.data
    data = resp.get_json()
    assert data["ok"] is True and data["status"] == "active" and data["skipped"] == []
    assert data["bot_link"] == f"https://t.me/RiverBot?start={page['edit_token']}"
    assert data["edit_url"].endswith(f"/edit/{page['edit_token']}")
    row = get_page_by_edit_token(page["edit_token"], tmp_db)
    assert row["sensitivity"] == "floods" and row["river_name"] == "Ohio River"
    sites = get_page_sites(page["id"], tmp_db)
    assert [(s["site_number"], s["origin"], s["parameter_code"]) for s in sites] == \
        [("03294500", "user", "00065")]
    assert [g["lid"] for g in get_page_gauges(page["id"], tmp_db)] == ["MLUK2"]


def test_save_queues_confirmation_to_owner(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    q = client.application.config["NOTIFICATION_QUEUE"]
    with patch("web.routes.validate_usgs_site", side_effect=_valid_site), \
         patch("web.routes.fetch_gauge_metadata", side_effect=_meta):
        client.post(f"/pin/{page['edit_token']}/save", json=SAVE_BODY)
    item = q.get_nowait()
    assert item["type"] == "direct"
    assert item["data"]["channel"] == "telegram" and item["data"]["channel_id"] == "99"
    assert "Ohio River" in item["data"]["message"] and "/settings" in item["data"]["message"]


def test_save_unowned_page_stays_pending_and_queues_nothing(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    q = client.application.config["NOTIFICATION_QUEUE"]
    with patch("web.routes.validate_usgs_site", side_effect=_valid_site), \
         patch("web.routes.fetch_gauge_metadata", side_effect=_meta):
        resp = client.post(f"/pin/{page['edit_token']}/save", json=SAVE_BODY)
    assert resp.get_json()["status"] == "pending"
    assert q.empty()


def test_save_rejects_zero_sources_and_bad_sensitivity(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    resp = client.post(f"/pin/{page['edit_token']}/save", json={**SAVE_BODY, "sources": []})
    assert resp.status_code == 400 and "source" in resp.get_json()["error"].lower()
    resp = client.post(f"/pin/{page['edit_token']}/save",
                       json={**SAVE_BODY, "sensitivity": "loud"})
    assert resp.status_code == 400


def test_save_skips_invalid_sources_but_fails_if_none_remain(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    with patch("web.routes.validate_usgs_site", return_value=(False, "", "not found")), \
         patch("web.routes.fetch_gauge_metadata", side_effect=_meta):
        resp = client.post(f"/pin/{page['edit_token']}/save", json=SAVE_BODY)
    assert resp.status_code == 200
    assert resp.get_json()["skipped"] == ["usgs:03294500"]
    with patch("web.routes.validate_usgs_site", return_value=(False, "", "not found")), \
         patch("web.routes.fetch_gauge_metadata", return_value=None):
        resp = client.post(f"/pin/{page['edit_token']}/save", json=SAVE_BODY)
    assert resp.status_code == 400


def test_pin_routes_do_not_require_admin_auth(client, tmp_db):
    # The fixture sends no Authorization header; a 401 here would mean the
    # endpoint was left out of PUBLIC_ENDPOINTS.
    page = create_pin_page(None, tmp_db)
    assert client.get(f"/pin/{page['edit_token']}").status_code == 200
    assert client.get("/pin").status_code == 302
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_pin.py -v`
Expected: FAIL — `AttributeError: module 'web.routes' has no attribute 'PIN_CREATE_LIMITER'`.

- [ ] **Step 3: Whitelist the endpoints**

In `web/auth.py` add to `PUBLIC_ENDPOINTS`:

```python
    "pin_start",
    "pin_map",
    "pin_discover",
    "pin_save",
```

- [ ] **Step 4: Add the routes**

In `web/routes.py` add imports:

```python
from flask import (render_template, current_app, request, redirect, url_for,
                   flash, jsonify, abort)
from db.models import (get_db, get_setting, set_setting, get_sites_with_health,
                       create_pin_page, get_page_by_edit_token, save_pin,
                       SENSITIVITY_LEVELS)
from monitor.pin_discovery import discover
from web.ratelimit import RateLimiter
```

Module level, after `UNRATED_DETAIL`:

```python
# Public pin routes call third-party APIs on a visitor's click, so they are
# throttled. Per process, per container: a brake, not a distributed quota.
PIN_CREATE_LIMITER = RateLimiter(limit=10, per_seconds=86400)
DISCOVER_TOKEN_LIMITER = RateLimiter(limit=20, per_seconds=3600)
DISCOVER_IP_LIMITER = RateLimiter(limit=60, per_seconds=3600)

SENSITIVITY_LABELS = [
    ("floods", "Floods only",
     "NOAA flood-category changes and severe highs"),
    ("unusual", "Floods and unusual levels",
     "Also unusually low or high readings for the time of year"),
    ("all", "Everything, including rapid changes",
     "Also fast rises and falls between readings"),
]


def bot_deep_link(edit_token, db_path):
    """Return the t.me link that binds this page to a Telegram chat, or None."""
    username = (get_setting("telegram_bot_username", db_path, default="") or "").strip()
    if not username:
        return None
    return f"https://t.me/{username}?start={edit_token}"


def _parse_coordinates(payload):
    """Return (lat, lon) floats from a JSON body, or None if unusable."""
    if not isinstance(payload, dict):
        return None
    try:
        lat = float(payload.get("lat"))
        lon = float(payload.get("lon"))
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


def _client_ip():
    return request.remote_addr or "unknown"
```

Inside `register_routes`, after `page_new`:

```python
    @app.route("/pin")
    def pin_start():
        """GET /pin — web-first entry: create a pending page and open its map."""
        if not PIN_CREATE_LIMITER.allow(_client_ip()):
            return "Too many new pages from this address today.\n", 429
        db_path = current_app.config["DB_PATH"]
        page = create_pin_page(None, db_path)
        return redirect(url_for("pin_map", edit_token=page["edit_token"]))

    def _pin_page_or_abort(edit_token):
        page = get_page_by_edit_token(edit_token, current_app.config["DB_PATH"])
        if not page:
            abort(404)
        if page["status"] == "stopped":
            abort(410)
        return page

    @app.route("/pin/<edit_token>")
    def pin_map(edit_token):
        """GET /pin/<edit_token> — the map screen."""
        db_path = current_app.config["DB_PATH"]
        page = _pin_page_or_abort(edit_token)
        return render_template(
            "pin.html", page=page, edit_token=edit_token,
            sensitivity_options=SENSITIVITY_LABELS,
            bot_link=bot_deep_link(edit_token, db_path),
        )

    @app.route("/pin/<edit_token>/discover", methods=["POST"])
    def pin_discover(edit_token):
        """POST /pin/<edit_token>/discover — propose gauges for a lat/lon."""
        db_path = current_app.config["DB_PATH"]
        _pin_page_or_abort(edit_token)
        if not DISCOVER_TOKEN_LIMITER.allow(edit_token) or not DISCOVER_IP_LIMITER.allow(_client_ip()):
            return jsonify({"error": "Too many searches; please wait a while."}), 429
        coords = _parse_coordinates(request.get_json(silent=True))
        if coords is None:
            return jsonify({"error": "lat and lon must be valid coordinates."}), 400
        reach_km = float(get_setting("discovery_reach_km", db_path, default="50"))
        radius = float(get_setting("search_radius_miles", db_path, default="25"))
        result = discover(coords[0], coords[1], reach_km=reach_km,
                          fallback_radius_miles=radius)
        return jsonify(result.to_dict())

    @app.route("/pin/<edit_token>/save", methods=["POST"])
    def pin_save(edit_token):
        """POST /pin/<edit_token>/save — store the pin and provision its sources."""
        db_path = current_app.config["DB_PATH"]
        page = _pin_page_or_abort(edit_token)
        payload = request.get_json(silent=True) or {}
        coords = _parse_coordinates(payload)
        if coords is None:
            return jsonify({"error": "lat and lon must be valid coordinates."}), 400
        sensitivity = payload.get("sensitivity", "unusual")
        if sensitivity not in SENSITIVITY_LEVELS:
            return jsonify({"error": "Unknown sensitivity level."}), 400
        sources = payload.get("sources") or []
        if not isinstance(sources, list) or not sources:
            return jsonify({"error": "Choose at least one source."}), 400

        usgs_sites, noaa_gauges, skipped = [], [], []
        for src in sources:
            kind = (src or {}).get("kind")
            ident = str((src or {}).get("id", "")).strip()
            if not ident:
                continue
            if kind == "usgs":
                code = str(src.get("parameter_code") or "00065")
                ok, name, _err = validate_usgs_site(ident, code)
                if ok:
                    usgs_sites.append({"site_number": ident, "station_name": name,
                                       "parameter_code": code})
                else:
                    skipped.append(f"usgs:{ident}")
            elif kind == "noaa":
                meta = fetch_gauge_metadata(ident)
                if meta:
                    noaa_gauges.append(meta)
                else:
                    skipped.append(f"noaa:{ident}")
        if not usgs_sites and not noaa_gauges:
            return jsonify({"error": "None of the chosen sources could be verified.",
                            "skipped": skipped}), 400

        river_name = (payload.get("river_name") or "").strip() or None
        save_pin(page["id"], coords[0], coords[1], river_name, sensitivity,
                 usgs_sites, noaa_gauges, db_path)
        saved = get_page_by_edit_token(edit_token, db_path)

        queue_ = current_app.config.get("NOTIFICATION_QUEUE")
        if saved["owner_chat_id"] is not None and queue_ is not None:
            names = [s["station_name"] or s["site_number"] for s in usgs_sites] + \
                    [g["station_name"] for g in noaa_gauges]
            listing = "\n".join(f"• {n}" for n in names)
            queue_.put({"type": "direct", "data": {
                "channel": "telegram",
                "channel_id": str(saved["owner_chat_id"]),
                "message": (
                    f"✓ You're set up for {river_name or 'your river'}. "
                    f"You'll hear about:\n{listing}\n\n"
                    "Send /settings any time to change gauges or sensitivity."
                ),
            }})

        return jsonify({
            "ok": True,
            "status": saved["status"],
            "skipped": skipped,
            "bot_link": bot_deep_link(edit_token, db_path),
            "edit_url": url_for("page_edit", edit_token=edit_token),
        })
```

Add the new settings fields to `SETTINGS_GROUPS` → `monitoring` section:

```python
                ("search_radius_miles", "Search Radius (miles)", "number"),
                ("discovery_reach_km", "Pin discovery reach along the river (km)", "number"),
                ("public_base_url", "Public base URL (e.g. https://river.example.com)", "text"),
```

- [ ] **Step 5: Create the map template**

```html
{# web/templates/pin.html #}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pick your spot on the river</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
  <style>
    #map { height: 60vh; min-height: 320px; }
    .tag { font-size: .75rem; }
  </style>
</head>
<body>
<div class="container py-3">
  <h1 class="h3">Pick your spot on the river</h1>
  <p class="text-muted">Click the map where you care about the water. We'll find the gauges on that stretch and you choose which to keep.</p>

  <div id="map" class="mb-3 rounded border"></div>

  <div class="d-flex gap-2 mb-3">
    <button id="find" class="btn btn-primary" disabled>Find gauges</button>
    <span id="status" class="align-self-center text-muted"></span>
  </div>

  <div id="results" hidden>
    <h2 class="h5" id="river"></h2>
    <p id="snapnote" class="text-warning small" hidden>No waterway found right here; showing the nearest gauges instead.</p>
    <ul id="candidates" class="list-group mb-3"></ul>

    <fieldset class="mb-3">
      <legend class="h6">How much do you want to hear?</legend>
      {% for value, label, help in sensitivity_options %}
      <div class="form-check">
        <input class="form-check-input" type="radio" name="sensitivity" id="s-{{ value }}"
               value="{{ value }}" {% if value == page.sensitivity %}checked{% endif %}>
        <label class="form-check-label" for="s-{{ value }}">
          <strong>{{ label }}</strong> <span class="text-muted small">— {{ help }}</span>
        </label>
      </div>
      {% endfor %}
    </fieldset>

    <button id="save" class="btn btn-success">Save</button>
    <span id="saveerr" class="text-danger ms-2"></span>
  </div>

  <div id="done" class="alert alert-success mt-3" hidden>
    <p class="mb-2">Saved.</p>
    <p id="done-pending" class="mb-2" hidden>
      To get alerts, connect Telegram:
      <a id="botlink" class="btn btn-sm btn-primary" target="_blank" rel="noopener">Send alerts to Telegram</a>
      <span id="nobot" class="text-muted small" hidden>(The Telegram bot is not configured yet — ask the site owner.)</span>
    </p>
    <p id="done-active" class="mb-0" hidden>Alerts will arrive in your Telegram chat. Send <code>/settings</code> there any time.</p>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
(function () {
  const token = {{ edit_token|tojson }};
  const initial = {{ ({"lat": page.pin_lat, "lon": page.pin_lon})|tojson }};
  const botLink = {{ bot_link|tojson }};
  const map = L.map('map').setView(initial.lat != null ? [initial.lat, initial.lon] : [39.5, -98.35],
                                   initial.lat != null ? 11 : 4);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
  let marker = null, pin = null, riverName = null;
  function place(latlng) {
    pin = { lat: +latlng.lat.toFixed(6), lon: +latlng.lng.toFixed(6) };
    if (marker) marker.setLatLng(latlng); else marker = L.marker(latlng).addTo(map);
    document.getElementById('find').disabled = false;
  }
  if (initial.lat != null) place(L.latLng(initial.lat, initial.lon));
  else if (navigator.geolocation) navigator.geolocation.getCurrentPosition(
    p => map.setView([p.coords.latitude, p.coords.longitude], 10), () => {});
  map.on('click', e => place(e.latlng));

  const status = document.getElementById('status');
  const list = document.getElementById('candidates');
  document.getElementById('find').addEventListener('click', async () => {
    status.textContent = 'Looking along the river…';
    list.innerHTML = '';
    document.getElementById('results').hidden = true;
    const resp = await fetch(`/pin/${token}/discover`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(pin)});
    const data = await resp.json();
    if (!resp.ok) { status.textContent = data.error || 'Search failed.'; return; }
    status.textContent = '';
    riverName = data.river_name;
    document.getElementById('river').textContent = data.river_name || 'Nearest gauges';
    document.getElementById('snapnote').hidden = data.snap === 'on_network';
    if (!data.candidates.length) {
      list.innerHTML = '<li class="list-group-item text-muted">No gauges found near here. Try another spot.</li>';
    }
    for (const c of data.candidates) {
      const li = document.createElement('li');
      li.className = 'list-group-item';
      li.innerHTML = `<label class="d-flex gap-2 align-items-start mb-0">
        <input type="checkbox" class="form-check-input mt-1" checked
               data-kind="${c.kind}" data-id="${c.id}" data-param="${c.parameter_code || ''}">
        <span><strong>${c.name}</strong>
          <span class="text-muted small">${c.kind.toUpperCase()} ${c.id} · ${c.distance_km} km</span>
          <span class="badge bg-secondary tag">${c.tag}</span></span></label>`;
      list.appendChild(li);
    }
    document.getElementById('results').hidden = false;
  });

  document.getElementById('save').addEventListener('click', async () => {
    const err = document.getElementById('saveerr');
    err.textContent = '';
    const sources = [...list.querySelectorAll('input:checked')].map(i => ({
      kind: i.dataset.kind, id: i.dataset.id,
      ...(i.dataset.param ? {parameter_code: i.dataset.param} : {})}));
    const sensitivity = document.querySelector('input[name=sensitivity]:checked').value;
    const resp = await fetch(`/pin/${token}/save`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...pin, river_name: riverName, sensitivity, sources})});
    const data = await resp.json();
    if (!resp.ok) { err.textContent = data.error || 'Could not save.'; return; }
    document.getElementById('results').hidden = true;
    document.getElementById('done').hidden = false;
    if (data.status === 'active') {
      document.getElementById('done-active').hidden = false;
    } else {
      document.getElementById('done-pending').hidden = false;
      if (data.bot_link) document.getElementById('botlink').href = data.bot_link;
      else { document.getElementById('botlink').hidden = true; document.getElementById('nobot').hidden = false; }
    }
  });
})();
</script>
</body>
</html>
```

- [ ] **Step 6: Run to verify pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_pin.py tests/web/test_auth.py tests/web/test_settings.py -v`
Expected: all PASS. If `test_auth.py` has a test enumerating `PUBLIC_ENDPOINTS` against registered routes, it should still pass; if it lists names explicitly, add the four new ones.

- [ ] **Step 7: Commit**

```bash
git add web/routes.py web/auth.py web/templates/pin.html tests/web/test_pin.py
git commit -m "feat(web): public pin map — discover gauges for a dropped pin and save them"
```

---

### Task 9: Sensitivity control on the edit page

**Files:**
- Modify: `web/routes.py` (add `page_set_sensitivity` route)
- Modify: `web/auth.py` (`PUBLIC_ENDPOINTS` += `"page_set_sensitivity"`)
- Modify: `web/templates/page_edit.html`
- Test: `tests/web/test_pin.py` (append)

**Interfaces:**
- Consumes: `set_page_sensitivity` (Task 3), `SENSITIVITY_LABELS` (Task 8).
- Produces: `POST /edit/<edit_token>/sensitivity` with form field `sensitivity`; flashes and redirects to the editor; 404 on unknown token; ignores an invalid value with a danger flash.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_pin.py`:

```python
ADMIN_AUTH = "Basic " + __import__("base64").b64encode(b"admin:testpass").decode()


def test_edit_page_shows_sensitivity_and_move_pin_link(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    resp = client.get(f"/edit/{page['edit_token']}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'name="sensitivity"' in body
    assert f"/pin/{page['edit_token']}" in body


def test_edit_page_sensitivity_post_updates_dial(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    resp = client.post(f"/edit/{page['edit_token']}/sensitivity",
                       data={"sensitivity": "all"}, follow_redirects=True)
    assert resp.status_code == 200
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["sensitivity"] == "all"


def test_edit_page_sensitivity_rejects_unknown_value(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    client.post(f"/edit/{page['edit_token']}/sensitivity", data={"sensitivity": "loud"})
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["sensitivity"] == "unusual"
    assert client.post("/edit/nope/sensitivity", data={"sensitivity": "all"}).status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_pin.py -k edit_page -v`
Expected: FAIL — 405 on the POST and `name="sensitivity"` missing from the editor markup.

- [ ] **Step 3: Implement route and template**

`web/auth.py`: add `"page_set_sensitivity"` to `PUBLIC_ENDPOINTS`.

`web/routes.py`, after `page_unsubscribe`:

```python
    @app.route("/edit/<edit_token>/sensitivity", methods=["POST"])
    def page_set_sensitivity(edit_token):
        """POST /edit/<edit_token>/sensitivity — set how much this page hears about."""
        from db.models import set_page_sensitivity
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        value = request.form.get("sensitivity", "")
        if value not in SENSITIVITY_LEVELS:
            flash("Unknown sensitivity level.", "danger")
        else:
            set_page_sensitivity(page["id"], value, db_path)
            flash("Sensitivity updated.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))
```

In `page_edit`, pass `sensitivity_options=SENSITIVITY_LABELS` to `render_template` (also in `page_search_gauges`, which renders the same template).

`web/templates/page_edit.html`: in the right-hand column, before the "Subscribe to Alerts" card:

```html
    <div class="card mb-3">
      <div class="card-header">Alert sensitivity</div>
      <div class="card-body">
        <form method="post" action="{{ url_for('page_set_sensitivity', edit_token=edit_token) }}">
          {% for value, label, help in sensitivity_options %}
          <div class="form-check">
            <input class="form-check-input" type="radio" name="sensitivity"
                   id="sens-{{ value }}" value="{{ value }}"
                   {% if page.sensitivity == value %}checked{% endif %}>
            <label class="form-check-label" for="sens-{{ value }}">
              <strong>{{ label }}</strong> <span class="text-muted small">— {{ help }}</span>
            </label>
          </div>
          {% endfor %}
          <button class="btn btn-sm btn-primary mt-2">Save</button>
        </form>
        {% if page.pin_lat is not none %}
        <p class="text-muted small mt-3 mb-0">
          Pinned on {{ page.river_name or "the map" }} ·
          <a href="{{ url_for('pin_map', edit_token=edit_token) }}">Move the pin or re-pick gauges</a>
        </p>
        {% else %}
        <p class="text-muted small mt-3 mb-0">
          <a href="{{ url_for('pin_map', edit_token=edit_token) }}">Pick a spot on the map</a>
          to have gauges proposed automatically.
        </p>
        {% endif %}
      </div>
    </div>
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_pin.py tests/web/test_pages.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add web/routes.py web/auth.py web/templates/page_edit.html tests/web/test_pin.py
git commit -m "feat(web): sensitivity dial and pin link on the page editor"
```

---

### Task 10: Telegram pin commands

**Files:**
- Create: `monitor/adapters/telegram_commands.py`
- Modify: `monitor/adapters/telegram.py` (`_handle_start`, `_run_bot`, `post_init` username capture)
- Create: `tests/monitor/test_telegram_commands.py`

**Interfaces:**
- Consumes: `create_pin_page`, `get_page_for_chat`, `bind_page_to_chat`, `set_page_sensitivity`, `set_page_status`, `get_page_sites`, `get_page_gauges`, `unlink_page_site`, `unlink_page_gauge`, `get_setting`, `set_setting`, `SENSITIVITY_LEVELS` (db); `SENSITIVITY_LABELS` is *not* imported from web — the module has its own short labels.
- Produces, in `monitor/adapters/telegram_commands.py`, synchronous helpers that return reply text (and are the unit under test):
  - `map_url(edit_token, db_path) -> str | None` — `<public_base_url>/pin/<token>`, None if the setting is empty.
  - `start_chat(chat_id, display_name, arg, db_path) -> str` — no arg: return the chat's existing page link, or create one and link it; with arg: bind and confirm.
  - `settings_reply(chat_id, db_path) -> str`
  - `sensitivity_keyboard(chat_id, db_path) -> tuple[str, list[list[tuple[str, str]]]]` — text and rows of `(label, callback_data)`; callback data is `sens:<level>`.
  - `apply_callback(chat_id, data, db_path) -> str` — handles `sens:<level>` and `rm:usgs:<site_id>` / `rm:noaa:<gauge_id>`; ignores payloads for pages the chat doesn't own.
  - `sources_keyboard(chat_id, db_path) -> tuple[str, list[list[tuple[str, str]]]]` — one row per source with a "Remove" button (`rm:<kind>:<id>`).
  - `set_status_reply(chat_id, status, db_path) -> str` — for `/pause`, `/resume`, `/stop`.
  - `class PinCommands(db_path)` with `register(app)` adding `CommandHandler`s for `settings`, `sensitivity`, `sources`, `pause`, `resume`, `stop` and a `CallbackQueryHandler` for `^(sens|rm):`. All async handlers call the sync helpers through `asyncio.to_thread`.
- Produces in `telegram.py`: `_handle_start` delegates to `start_chat`; `_run_bot` calls `PinCommands(self.db_path).register(self._app)` and passes a `post_init` that stores `telegram_bot_username`.

Reply texts (exact strings the tests check):

```python
NO_BASE_URL = ("The site address isn't configured yet, so I can't send a map link. "
               "Ask the site owner to set the public base URL.")
NO_PAGE = "You don't have a river set up yet. Send /start to pick one on the map."
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/monitor/test_telegram_commands.py
from db.models import (create_pin_page, get_page_by_edit_token, get_page_for_chat,
                       get_page_gauges, get_page_sites, save_pin, set_setting)
from monitor.adapters.telegram_commands import (
    NO_BASE_URL, NO_PAGE, apply_callback, map_url, sensitivity_keyboard,
    set_status_reply, settings_reply, sources_keyboard, start_chat,
)

USGS = [{"site_number": "03294500", "station_name": "Ohio at Louisville",
         "parameter_code": "00065"}]
NOAA = [{"lid": "MLUK2", "station_name": "McAlpine Upper", "action_stage": 21.0,
         "minor_flood_stage": None, "moderate_flood_stage": None,
         "major_flood_stage": None}]


def _base(tmp_db):
    set_setting("public_base_url", "https://river.example.com/", tmp_db)


def test_map_url_uses_base_url_without_double_slash(tmp_db):
    _base(tmp_db)
    assert map_url("tok", tmp_db) == "https://river.example.com/pin/tok"
    set_setting("public_base_url", "", tmp_db)
    assert map_url("tok", tmp_db) is None


def test_start_without_arg_creates_owned_pending_page_and_links_map(tmp_db):
    _base(tmp_db)
    reply = start_chat(42, "Ann", "", tmp_db)
    page = get_page_for_chat(42, tmp_db)
    assert page["status"] == "pending"
    assert f"/pin/{page['edit_token']}" in reply


def test_start_twice_returns_the_same_page(tmp_db):
    _base(tmp_db)
    start_chat(42, "Ann", "", tmp_db)
    first = get_page_for_chat(42, tmp_db)
    reply = start_chat(42, "Ann", "", tmp_db)
    assert get_page_for_chat(42, tmp_db)["id"] == first["id"]
    assert first["edit_token"] in reply


def test_start_without_base_url_explains(tmp_db):
    reply = start_chat(42, "Ann", "", tmp_db)
    assert NO_BASE_URL in reply
    assert get_page_for_chat(42, tmp_db) is not None   # page still created


def test_start_with_token_binds_web_first_page(tmp_db):
    _base(tmp_db)
    page = create_pin_page(None, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio River", "unusual", USGS, [], tmp_db)
    reply = start_chat(42, "Ann", page["edit_token"], tmp_db)
    row = get_page_by_edit_token(page["edit_token"], tmp_db)
    assert row["owner_chat_id"] == 42 and row["status"] == "active"
    assert "Ohio River" in reply


def test_start_with_bad_token_reports_it(tmp_db):
    _base(tmp_db)
    reply = start_chat(42, "Ann", "nope", tmp_db)
    assert "couldn't find" in reply.lower()


def test_settings_reply_links_editor_or_explains(tmp_db):
    _base(tmp_db)
    assert settings_reply(42, tmp_db) == NO_PAGE
    start_chat(42, "Ann", "", tmp_db)
    page = get_page_for_chat(42, tmp_db)
    assert f"/edit/{page['edit_token']}" in settings_reply(42, tmp_db)


def test_sensitivity_keyboard_and_callback(tmp_db):
    _base(tmp_db)
    start_chat(42, "Ann", "", tmp_db)
    text, rows = sensitivity_keyboard(42, tmp_db)
    assert [cb for row in rows for _, cb in row] == ["sens:floods", "sens:unusual", "sens:all"]
    reply = apply_callback(42, "sens:all", tmp_db)
    assert get_page_for_chat(42, tmp_db)["sensitivity"] == "all"
    assert "Everything" in reply


def test_sources_keyboard_lists_and_removes(tmp_db):
    _base(tmp_db)
    start_chat(42, "Ann", "", tmp_db)
    page = get_page_for_chat(42, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio River", "unusual", USGS, NOAA, tmp_db)
    text, rows = sources_keyboard(42, tmp_db)
    assert "Ohio at Louisville" in text and "McAlpine Upper" in text
    callbacks = [cb for row in rows for _, cb in row]
    site_id = get_page_sites(page["id"], tmp_db)[0]["id"]
    gauge_id = get_page_gauges(page["id"], tmp_db)[0]["id"]
    assert callbacks == [f"rm:usgs:{site_id}", f"rm:noaa:{gauge_id}"]
    apply_callback(42, f"rm:usgs:{site_id}", tmp_db)
    assert get_page_sites(page["id"], tmp_db) == []
    assert len(get_page_gauges(page["id"], tmp_db)) == 1


def test_callback_for_another_chats_page_is_ignored(tmp_db):
    _base(tmp_db)
    start_chat(42, "Ann", "", tmp_db)
    page = get_page_for_chat(42, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio River", "unusual", USGS, [], tmp_db)
    site_id = get_page_sites(page["id"], tmp_db)[0]["id"]
    reply = apply_callback(7, f"rm:usgs:{site_id}", tmp_db)   # chat 7 owns nothing
    assert reply == NO_PAGE
    assert len(get_page_sites(page["id"], tmp_db)) == 1


def test_pause_resume_stop(tmp_db):
    _base(tmp_db)
    start_chat(42, "Ann", "", tmp_db)
    page = get_page_for_chat(42, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio River", "unusual", USGS, [], tmp_db)
    set_status_reply(42, "paused", tmp_db)
    assert get_page_for_chat(42, tmp_db)["status"] == "paused"
    set_status_reply(42, "active", tmp_db)
    assert get_page_for_chat(42, tmp_db)["status"] == "active"
    set_status_reply(42, "stopped", tmp_db)
    assert get_page_for_chat(42, tmp_db) is None
    assert set_status_reply(42, "paused", tmp_db) == NO_PAGE
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_telegram_commands.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitor.adapters.telegram_commands'`.

- [ ] **Step 3: Implement the commands module**

```python
# monitor/adapters/telegram_commands.py
"""Telegram commands for pin pages: the chat is the account.

Each chat owns at most one live page. The synchronous helpers below do all
the database work and return the reply text; the async handlers in
:class:`PinCommands` are thin wrappers that run them in a thread so psycopg2
never blocks the bot's event loop. Keeping the helpers synchronous is what
lets the tests exercise every command without starting a bot.
"""

import asyncio
import logging

from db.models import (
    SENSITIVITY_LEVELS,
    bind_page_to_chat,
    create_pin_page,
    get_page_for_chat,
    get_page_gauges,
    get_page_sites,
    get_setting,
    set_page_sensitivity,
    set_page_status,
    unlink_page_gauge,
    unlink_page_site,
)

logger = logging.getLogger(__name__)

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import CallbackQueryHandler, CommandHandler
    TELEGRAM_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the package
    TELEGRAM_AVAILABLE = False

NO_BASE_URL = ("The site address isn't configured yet, so I can't send a map link. "
               "Ask the site owner to set the public base URL.")
NO_PAGE = "You don't have a river set up yet. Send /start to pick one on the map."
TOKEN_NOT_FOUND = ("I couldn't find that page. Open the map link again and tap "
                   "\"Send alerts to Telegram\".")

SENSITIVITY_TEXT = {
    "floods": "Floods only",
    "unusual": "Floods and unusual levels",
    "all": "Everything, including rapid changes",
}


def _base_url(db_path):
    return (get_setting("public_base_url", db_path, default="") or "").strip().rstrip("/")


def map_url(edit_token, db_path=None):
    """Absolute link to the map screen for `edit_token`, or None when unconfigured."""
    base = _base_url(db_path)
    return f"{base}/pin/{edit_token}" if base else None


def edit_url(edit_token, db_path=None):
    base = _base_url(db_path)
    return f"{base}/edit/{edit_token}" if base else None


def _link_or_explain(url):
    return url if url else NO_BASE_URL


def start_chat(chat_id, display_name, arg, db_path=None):
    """Handle /start [token]: bind a web-first page, or hand out the map link."""
    arg = (arg or "").strip()
    if arg:
        page = bind_page_to_chat(arg, chat_id, display_name, db_path)
        if page is None:
            return TOKEN_NOT_FOUND
        if page["status"] == "active":
            river = page["river_name"] or "your river"
            return (f"✓ Connected. You'll get alerts for {river} here. "
                    "Send /settings any time to change gauges or sensitivity.")
        return ("✓ Connected. Finish picking your spot on the map: "
                f"{_link_or_explain(map_url(page['edit_token'], db_path))}")
    page = get_page_for_chat(chat_id, db_path)
    if page is None:
        page = create_pin_page(int(chat_id), db_path)
        bind_page_to_chat(page["edit_token"], chat_id, display_name, db_path)
    link = _link_or_explain(map_url(page["edit_token"], db_path))
    if page["status"] == "pending":
        return ("Welcome! Pick the spot on the river you care about and I'll "
                f"watch the gauges there:\n{link}")
    return (f"You're already set up for {page['river_name'] or 'your river'}. "
            f"Change it here:\n{link}\nOther commands: /settings /sensitivity "
            "/sources /pause /resume /stop")


def settings_reply(chat_id, db_path=None):
    """Handle /settings: link to the page editor."""
    page = get_page_for_chat(chat_id, db_path)
    if page is None:
        return NO_PAGE
    return ("Manage your gauges and sensitivity here:\n"
            f"{_link_or_explain(edit_url(page['edit_token'], db_path))}")


def sensitivity_keyboard(chat_id, db_path=None):
    """Handle /sensitivity: (text, rows of (label, callback_data))."""
    page = get_page_for_chat(chat_id, db_path)
    if page is None:
        return NO_PAGE, []
    current = SENSITIVITY_TEXT.get(page["sensitivity"], page["sensitivity"])
    rows = [[(SENSITIVITY_TEXT[level], f"sens:{level}")] for level in SENSITIVITY_LEVELS]
    return f"How much do you want to hear? Now: {current}", rows


def sources_keyboard(chat_id, db_path=None):
    """Handle /sources: list the page's gauges with a Remove button each."""
    page = get_page_for_chat(chat_id, db_path)
    if page is None:
        return NO_PAGE, []
    lines, rows = [], []
    for site in get_page_sites(page["id"], db_path):
        name = site["station_name"] or site["site_number"]
        lines.append(f"• {name} (USGS {site['site_number']})")
        rows.append([(f"Remove {name}"[:60], f"rm:usgs:{site['id']}")])
    for gauge in get_page_gauges(page["id"], db_path):
        lines.append(f"• {gauge['station_name']} (NOAA {gauge['lid']})")
        rows.append([(f"Remove {gauge['station_name']}"[:60], f"rm:noaa:{gauge['id']}")])
    if not lines:
        return "No gauges yet. Send /start to pick a spot on the map.", []
    return "You're watching:\n" + "\n".join(lines), rows


def apply_callback(chat_id, data, db_path=None):
    """Handle an inline-button press; returns the reply text."""
    page = get_page_for_chat(chat_id, db_path)
    if page is None:
        return NO_PAGE
    parts = (data or "").split(":")
    if parts[0] == "sens" and len(parts) == 2 and parts[1] in SENSITIVITY_LEVELS:
        set_page_sensitivity(page["id"], parts[1], db_path)
        return f"✓ Sensitivity set to: {SENSITIVITY_TEXT[parts[1]]}"
    if parts[0] == "rm" and len(parts) == 3 and parts[2].isdigit():
        row_id = int(parts[2])
        if parts[1] == "usgs":
            unlink_page_site(page["id"], row_id, db_path)
        elif parts[1] == "noaa":
            unlink_page_gauge(page["id"], row_id, db_path)
        else:
            return "I didn't understand that button."
        text, _ = sources_keyboard(chat_id, db_path)
        return "✓ Removed.\n" + text
    return "I didn't understand that button."


def set_status_reply(chat_id, status, db_path=None):
    """Handle /pause, /resume, /stop."""
    page = get_page_for_chat(chat_id, db_path)
    if page is None:
        return NO_PAGE
    set_page_status(page["id"], status, db_path)
    if status == "paused":
        return "⏸ Alerts paused. Send /resume when you want them back."
    if status == "stopped":
        return ("Stopped. Your page is closed and its gauges released. "
                "Send /start whenever you want to set up a new one.")
    return "▶️ Alerts resumed."


class PinCommands:
    """Registers the pin-page command handlers on a python-telegram-bot Application."""

    def __init__(self, db_path=None):
        self.db_path = db_path

    def register(self, app):
        app.add_handler(CommandHandler("settings", self._settings))
        app.add_handler(CommandHandler("sensitivity", self._sensitivity))
        app.add_handler(CommandHandler("sources", self._sources))
        app.add_handler(CommandHandler("pause", self._pause))
        app.add_handler(CommandHandler("resume", self._resume))
        app.add_handler(CommandHandler("stop", self._stop))
        app.add_handler(CallbackQueryHandler(self._callback, pattern=r"^(sens|rm):"))

    @staticmethod
    def _markup(rows):
        if not rows:
            return None
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(label, callback_data=data) for label, data in row]
             for row in rows])

    async def _settings(self, update, context):
        reply = await asyncio.to_thread(settings_reply, update.effective_chat.id, self.db_path)
        await update.message.reply_text(reply)

    async def _sensitivity(self, update, context):
        text, rows = await asyncio.to_thread(
            sensitivity_keyboard, update.effective_chat.id, self.db_path)
        await update.message.reply_text(text, reply_markup=self._markup(rows))

    async def _sources(self, update, context):
        text, rows = await asyncio.to_thread(
            sources_keyboard, update.effective_chat.id, self.db_path)
        await update.message.reply_text(text, reply_markup=self._markup(rows))

    async def _pause(self, update, context):
        reply = await asyncio.to_thread(
            set_status_reply, update.effective_chat.id, "paused", self.db_path)
        await update.message.reply_text(reply)

    async def _resume(self, update, context):
        reply = await asyncio.to_thread(
            set_status_reply, update.effective_chat.id, "active", self.db_path)
        await update.message.reply_text(reply)

    async def _stop(self, update, context):
        reply = await asyncio.to_thread(
            set_status_reply, update.effective_chat.id, "stopped", self.db_path)
        await update.message.reply_text(reply)

    async def _callback(self, update, context):
        query = update.callback_query
        await query.answer()
        reply = await asyncio.to_thread(
            apply_callback, query.message.chat.id, query.data, self.db_path)
        await query.edit_message_text(reply)
```

- [ ] **Step 4: Wire the adapter**

In `monitor/adapters/telegram.py`:

```python
from db.models import (..., set_setting)          # extend the existing import
from monitor.adapters.telegram_commands import PinCommands, start_chat
```

Replace `_handle_start`:

```python
    async def _handle_start(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        """Handle /start [token]: create or bind this chat's pin page and send the map link."""
        chat_id = str(update.effective_chat.id)
        name = update.effective_user.full_name or "Telegram User"
        args = getattr(context, "args", None) or []
        await asyncio.to_thread(_record_pending_registration, chat_id, self.db_path)
        reply = await asyncio.to_thread(
            start_chat, chat_id, name, args[0] if args else "", self.db_path)
        await update.message.reply_text(reply)
```

In `_run_bot`, build the application with a `post_init` and register the commands:

```python
        async def _remember_username(app):
            try:
                me = await app.bot.get_me()
                await asyncio.to_thread(
                    set_setting, "telegram_bot_username", me.username or "", self.db_path)
            except Exception:
                logger.exception("Could not read the bot's username")

        self._app = (
            Application.builder()
            .token(token)
            .post_init(_remember_username)
            .build()
        )
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("subscribe", self._handle_subscribe))
        self._app.add_handler(CommandHandler("unsubscribe", self._handle_unsubscribe))
        self._app.add_handler(CommandHandler("mypages", self._handle_mypages))
        PinCommands(self.db_path).register(self._app)
```

- [ ] **Step 5: Run to verify pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_telegram_commands.py tests/monitor/test_telegram_adapter.py -v`
Expected: all PASS (the adapter supervisor tests patch `_run_bot`, so wiring changes do not disturb them).

- [ ] **Step 6: Commit**

```bash
git add monitor/adapters/telegram_commands.py monitor/adapters/telegram.py tests/monitor/test_telegram_commands.py
git commit -m "feat(telegram): chat-owned pin pages — /start map link, settings, sensitivity, sources, pause/resume/stop"
```

---

### Task 11: Admin portal visibility

**Files:**
- Modify: `db/models.py` (`get_sites_with_health` adds `page_count`)
- Modify: `web/routes.py` (`admin_pages` query)
- Modify: `web/templates/sites.html`, `web/templates/admin_pages.html`
- Test: `tests/web/test_sites.py`, `tests/web/test_pages.py` (append)

**Interfaces:**
- Produces: `get_sites_with_health` rows gain `page_count` (pages with status active/paused referencing the site) and already carry `origin`. `admin_pages` rows gain `site_count` and expose `owner_chat_id`, `river_name`, `status`, `pin_lat`, `pin_lon`.

There is no admin NOAA gauge list page today, so the origin badge for NOAA gauges appears only on the pages that list them (the page editor shows them per page). Adding an admin NOAA list is out of scope.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_sites.py` (it already has an authenticated `client` fixture; if the helper names differ, mirror the existing tests in that file):

```python
def test_sites_list_shows_origin_and_page_count(client, tmp_db):
    from db.models import create_pin_page, save_pin
    page = create_pin_page(1, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio", "unusual",
             [{"site_number": "03294500", "station_name": "Ohio at Louisville",
               "parameter_code": "00065"}], [], tmp_db)
    body = client.get("/sites").data.decode()
    assert "User-added" in body
    assert "1 page" in body
```

Append to `tests/web/test_pages.py`:

```python
def test_admin_pages_shows_pin_owner_and_status(client, tmp_db):
    from db.models import create_pin_page, save_pin
    page = create_pin_page(4242, tmp_db)
    save_pin(page["id"], 38.28, -85.76, "Ohio River", "floods",
             [{"site_number": "03294500", "station_name": "Ohio", "parameter_code": "00065"}],
             [], tmp_db)
    body = client.get("/admin/pages").data.decode()
    assert "Ohio River" in body and "4242" in body
    assert "38.28" in body and "-85.76" in body
    assert "active" in body.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_sites.py tests/web/test_pages.py -v`
Expected: the two new tests FAIL on missing markup.

- [ ] **Step 3: Implement**

`db/models.py` `get_sites_with_health` query:

```sql
SELECT s.*,
       (s.last_success_at IS NULL
        OR s.last_success_at < NOW() - (%s * INTERVAL '1 hour')) AS stale,
       (SELECT COUNT(*) FROM page_sites ps
          JOIN user_pages up ON up.id = ps.page_id
         WHERE ps.site_id = s.id AND up.status IN ('active', 'paused')) AS page_count
FROM sites s ORDER BY s.id
```

`web/templates/sites.html`: in the Station Name cell, after the name:

```html
    <td>{{ s.station_name or "—" }}
      {% if s.origin == "user" %}<span class="badge bg-info text-dark ms-1">User-added</span>{% endif %}
      <span class="text-muted small ms-1">{{ s.page_count }} page{{ "" if s.page_count == 1 else "s" }}</span>
    </td>
```

`web/routes.py` `admin_pages` query:

```sql
SELECT up.*,
       COUNT(DISTINCT png.noaa_gauge_id) AS gauge_count,
       COUNT(DISTINCT pst.site_id) AS site_count,
       COUNT(DISTINCT ps.id) AS subscriber_count
FROM user_pages up
LEFT JOIN page_noaa_gauges png ON png.page_id = up.id
LEFT JOIN page_sites pst ON pst.page_id = up.id
LEFT JOIN page_subscribers ps ON ps.page_id = up.id AND ps.status='active'
GROUP BY up.id
ORDER BY up.created_at DESC
```

`web/templates/admin_pages.html`: replace the table header and row:

```html
  <thead><tr>
    <th>Name</th><th>Pin</th><th>Owner</th><th>Sources</th><th>Subscribers</th>
    <th>Created</th><th>Status</th><th></th>
  </tr></thead>
  <tbody>
  {% for p in pages %}
  <tr class="{{ '' if p.active else 'table-secondary text-muted' }}">
    <td>
      {{ p.page_name }}{% if p.river_name %} <span class="text-muted">— {{ p.river_name }}</span>{% endif %}<br>
      <small class="text-muted">
        <a href="{{ url_for('page_view', public_token=p.public_token) }}" target="_blank">View ↗</a>
      </small>
    </td>
    <td><small>{% if p.pin_lat is not none %}{{ "%.2f"|format(p.pin_lat) }}, {{ "%.2f"|format(p.pin_lon) }}{% else %}—{% endif %}</small></td>
    <td><small>{{ p.owner_chat_id if p.owner_chat_id is not none else "admin" }}</small></td>
    <td>{{ p.site_count }} USGS · {{ p.gauge_count }} NOAA</td>
    <td>{{ p.subscriber_count }}</td>
    <td><small>{{ p.created_at }}</small></td>
    <td>
      <span class="badge bg-{{ 'success' if p.active else 'secondary' }}">
        {{ 'Active' if p.active else 'Disabled' }}
      </span>
      <span class="badge bg-light text-dark">{{ p.status }}</span>
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
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add db/models.py web/routes.py web/templates/sites.html web/templates/admin_pages.html tests/web/test_sites.py tests/web/test_pages.py
git commit -m "feat(admin): show source origin, page references, and pin details"
```

---

### Task 12: Documentation and full-suite verification

**Files:**
- Modify: `CLAUDE.md` (Architecture: module layout, data flow, settings list)
- Modify: `.env.example` — no change needed (`public_base_url` is a DB setting); confirm and leave.

- [ ] **Step 1: Update CLAUDE.md**

In the module layout add:

```
  pin_discovery.py      — Pin → gauges on the same main stem (USGS NLDI), bbox fallback
  retirement.py         — Hourly sweep: deactivate unreferenced user-origin sources
  adapters/
    telegram_commands.py — /start map link, /settings, /sensitivity, /sources, /pause, /resume, /stop
web/
  ratelimit.py          — In-process sliding-window limiter for the public pin routes
```

Under **Alert routing** add a paragraph:

```
Pin pages (`user_pages.owner_chat_id` set) are created from Telegram `/start`
or from `GET /pin`. Saving the pin provisions the chosen USGS sites and NOAA
gauges with `origin='user'`; `monitor/retirement.py` deactivates them once no
active or paused page references them. Each page has a `sensitivity` dial
(`floods` / `unusual` / `all`) applied by the dispatcher through
`monitor.scheduler.alert_allowed`, and a lifecycle `status`
(`pending` / `active` / `paused` / `stopped`); only `active` pages receive alerts.
```

Add `discovery_reach_km`, `public_base_url`, `telegram_bot_username` to the key-settings list, and NLDI to the USGS API section:

```
Pin discovery uses the USGS Network Linked Data Index
(`https://api.water.usgs.gov/nldi/linked-data`): `comid/position` snaps a point
to a flowline; `comid/{comid}/navigation/UM|DM/nwissite` lists gauges along the
main stem.
```

- [ ] **Step 2: Run the whole suite**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test`
Expected: all tests PASS, zero failures. Fix anything that regressed before committing.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: pin-on-a-map onboarding architecture notes"
```

---

## Self-review notes

- **Spec coverage:** §1 user flow → Tasks 8, 9, 10; §2 discovery → Tasks 5, 6; §3 data model, provisioning, retirement, sensitivity → Tasks 1, 2, 3, 4; §4 routes, rate limits, Telegram, admin → Tasks 7, 8, 10, 11; §5 tests → each task. The spec's "NOAA gauge list" origin badge has no admin page to live on and is noted as out of scope in Task 11.
- **Deviation from spec wording:** the spec placed the sensitivity filter "in both pollers"; routing actually happens in the dispatcher, so `alert_allowed` lives in `monitor/scheduler.py` as specified but is called from `monitor/dispatcher.py` (Task 2). Behaviour is identical: one poll per gauge, filter per page.
- **Owner as subscriber:** alerts route through `page_subscribers`, so `bind_page_to_chat` (Task 3) always inserts the owning chat as an active telegram subscriber. Without this the owner would never receive anything.
- **Confirmation message:** the Flask process has no adapter handle, so `pin_save` enqueues a `direct` item (Task 2) that the dispatcher delivers.
