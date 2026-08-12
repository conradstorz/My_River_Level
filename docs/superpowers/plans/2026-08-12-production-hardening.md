# River Monitor Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make River Monitor safe to expose publicly and correct in what it sends — authenticated portal, verified webhooks, per-user gauge routing, rise/fall alerting, supervised threads, and a real judgement of each gauge's flood-forecast quality.

**Architecture:** Keep the existing thread-per-concern layout in `main.py`. Add a thread registry so a new unauthenticated `/healthz` route can report liveness and Docker can restart a half-dead container. Introduce a `page_sites` link table so USGS alerts route to the subscribers of the pages that reference that site, mirroring how `page_noaa_gauges` already routes NOAA alerts. Add a forecast-archiving thread that stores NOAA forecast points and later scores them against what was actually observed, producing a per-gauge letter grade.

**Tech Stack:** Python 3.11, Flask 3.1, psycopg2, waitress, python-telegram-bot 22.8, twilio 9.10, pytest.

## Global Constraints

- PostgreSQL only. Every DB helper lives in `db/models.py`, opens and closes its own connection, and takes an optional `db_path` (a PostgreSQL URL) as its last argument.
- Schema changes go in `SCHEMA_STATEMENTS` (new tables) or `MIGRATION_STATEMENTS` (new columns on existing tables, using `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`). The production database already exists with data — never write a migration that drops or rewrites an existing column.
- Any new table MUST be added to `_DROP_ALL` in `tests/conftest.py`, or tests leak state between runs.
- Tests run with `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test`. Plain `pytest` on the host cannot reach the database. Add `--build` after changing `requirements.txt`.
- Never chain shell commands with `&&`, `||`, `|`, or `;`. One command per call.
- All 167 existing tests must still pass. If a behavior change legitimately invalidates an existing test, update that test and say so in the commit message — do not delete coverage.
- New settings keys must be added to `DEFAULT_SETTINGS` in `db/models.py` so `init_db` seeds them.
- Secrets are read from environment variables or the `settings` table. Never hardcode a credential or commit one.

---

## File Ownership

Tasks run in three phases. Within a phase, tasks touch disjoint files and may run in parallel. **Do not modify a file owned by another task.**

| Phase | Task | Owns |
|---|---|---|
| 0 | 1. Foundations | `db/models.py`, `tests/conftest.py`, `requirements.txt`, `tests/db/test_models.py` |
| 1 | 2. Auth & webhooks | `web/auth.py`, `web/health.py`, `web/app.py`, `web/routes.py`, `tests/web/*` |
| 1 | 3. Telegram adapter | `monitor/adapters/telegram.py`, `tests/monitor/test_telegram_adapter.py` |
| 1 | 4. Polling & dispatch | `monitor/polling.py`, `monitor/dispatcher.py`, `monitor/noaa_polling.py`, `monitor/trend.py`, `tests/monitor/test_polling.py`, `tests/monitor/test_dispatcher.py`, `tests/monitor/test_noaa_polling.py`, `tests/monitor/test_trend.py` |
| 1 | 5. Forecast & grading | `monitor/noaa_client.py`, `monitor/gauge_quality.py`, `monitor/forecast_polling.py`, `tests/monitor/test_noaa_client.py`, `tests/monitor/test_gauge_quality.py`, `tests/monitor/test_forecast_polling.py` |
| 2 | 6. Portal UI | `web/routes.py`, `web/templates/*`, `tests/web/test_sites.py`, `tests/web/test_pages.py` |
| 3 | 7. Runtime & deploy | `main.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`, `CLAUDE.md` |

---

## Task 1: Foundations — schema, helpers, pinned dependencies

**Files:**
- Modify: `db/models.py`
- Modify: `tests/conftest.py` (`_DROP_ALL`)
- Modify: `requirements.txt`
- Test: `tests/db/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces (every later task depends on these exact signatures):

```python
# New settings keys seeded into DEFAULT_SETTINGS
"facebook_app_secret": ""
"rate_change_threshold_ft": "2.0"
"rate_change_threshold_pct": "25"
"rate_change_window_hours": "6"
"rate_change_min_interval_hours": "6"
"site_stale_hours": "6"
"forecast_poll_hours": "6"

# page <-> USGS site linking
link_page_site(page_id, site_id, db_path=None) -> None
unlink_page_site(page_id, site_id, db_path=None) -> None
get_page_sites(page_id, db_path=None) -> list[dict]          # rows from sites
get_pages_for_site(site_id, db_path=None) -> list[dict]      # active user_pages
get_page_subscribers_for_site(site_id, db_path=None) -> list[dict]  # active page_subscribers

# site health
record_site_fetch_success(site_id, db_path=None) -> None
record_site_fetch_error(site_id, message, db_path=None) -> None
get_sites_with_health(db_path=None) -> list[dict]
    # each dict: sites.* plus "stale" (bool), "last_success_at", "last_error", "last_error_at"
    # "stale" is True when last_success_at is NULL or older than the
    # site_stale_hours setting.

# NOAA observation history (needed to score forecasts)
record_noaa_observation(lid, stage, observed_at=None, db_path=None) -> None
    # observed_at defaults to NOW(); duplicate (lid, observed_at) is ignored
get_noaa_observations(lid, db_path=None) -> list[dict]
    # keys: lid, observed_at (datetime), stage (float); ascending by observed_at

# NOAA forecast archive
record_forecast_points(lid, issued_at, points, db_path=None) -> int
    # points: list[dict] with keys "valid_at" (datetime) and "stage" (float)
    # returns number of rows actually inserted; duplicates are ignored
get_forecast_points(lid, db_path=None) -> list[dict]
    # keys: lid, issued_at (datetime), valid_at (datetime), predicted_stage (float)

# gauge quality persistence
set_gauge_quality(lid, grade, detail, db_path=None) -> None
get_gauge_quality(lid, db_path=None) -> dict | None
    # keys: grade (str), detail (str), checked_at (str) — None if never scored
```

New tables:

```sql
CREATE TABLE IF NOT EXISTS page_sites (
    page_id INTEGER NOT NULL REFERENCES user_pages(id),
    site_id INTEGER NOT NULL REFERENCES sites(id),
    PRIMARY KEY (page_id, site_id)
)

CREATE TABLE IF NOT EXISTS noaa_observations (
    id SERIAL PRIMARY KEY,
    lid TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stage DOUBLE PRECISION NOT NULL,
    UNIQUE (lid, observed_at)
)

CREATE TABLE IF NOT EXISTS gauge_forecasts (
    id SERIAL PRIMARY KEY,
    lid TEXT NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL,
    valid_at TIMESTAMPTZ NOT NULL,
    predicted_stage DOUBLE PRECISION NOT NULL,
    UNIQUE (lid, issued_at, valid_at)
)
```

New columns (via `MIGRATION_STATEMENTS`, run inside `init_db` after `SCHEMA_STATEMENTS`):

```sql
ALTER TABLE sites ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ
ALTER TABLE sites ADD COLUMN IF NOT EXISTS last_error TEXT NOT NULL DEFAULT ''
ALTER TABLE sites ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMPTZ
ALTER TABLE noaa_gauges ADD COLUMN IF NOT EXISTS quality_grade TEXT NOT NULL DEFAULT ''
ALTER TABLE noaa_gauges ADD COLUMN IF NOT EXISTS quality_detail TEXT NOT NULL DEFAULT ''
ALTER TABLE noaa_gauges ADD COLUMN IF NOT EXISTS quality_checked_at TIMESTAMPTZ
CREATE INDEX IF NOT EXISTS idx_site_conditions_site_id ON site_conditions (site_id, id DESC)
CREATE INDEX IF NOT EXISTS idx_notifications_site_trigger ON notifications (site_id, trigger_type, id DESC)
```

- [ ] **Step 1: Write failing tests for the new helpers**

Add to `tests/db/test_models.py`:

```python
def test_link_and_get_page_sites(tmp_db):
    from db.models import create_user_page, get_page_by_edit_token, link_page_site, get_page_sites
    _, edit = create_user_page("Test", tmp_db)
    page = get_page_by_edit_token(edit, tmp_db)
    conn = get_db(tmp_db); cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('123', 'A') RETURNING id")
    site_id = cur.fetchone()["id"]; conn.commit(); cur.close(); conn.close()
    link_page_site(page["id"], site_id, tmp_db)
    rows = get_page_sites(page["id"], tmp_db)
    assert [r["site_number"] for r in rows] == ["123"]


def test_get_page_subscribers_for_site_only_returns_active(tmp_db):
    from db.models import (create_user_page, get_page_by_edit_token, link_page_site,
                           add_page_subscriber, set_page_subscriber_status,
                           get_page_subscribers_for_site)
    _, edit = create_user_page("Test", tmp_db)
    page = get_page_by_edit_token(edit, tmp_db)
    conn = get_db(tmp_db); cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('123', 'A') RETURNING id")
    site_id = cur.fetchone()["id"]; conn.commit(); cur.close(); conn.close()
    link_page_site(page["id"], site_id, tmp_db)
    add_page_subscriber(page["id"], "telegram", "111", "Yes", tmp_db)
    add_page_subscriber(page["id"], "telegram", "222", "No", tmp_db)
    set_page_subscriber_status(page["id"], "telegram", "222", "unsubscribed", tmp_db)
    subs = get_page_subscribers_for_site(site_id, tmp_db)
    assert [s["channel_id"] for s in subs] == ["111"]


def test_site_health_marks_stale_when_never_polled(tmp_db):
    from db.models import get_sites_with_health
    conn = get_db(tmp_db); cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('123', 'A')")
    conn.commit(); cur.close(); conn.close()
    rows = get_sites_with_health(tmp_db)
    assert rows[0]["stale"] is True


def test_record_site_fetch_success_clears_stale(tmp_db):
    from db.models import record_site_fetch_success, get_sites_with_health
    conn = get_db(tmp_db); cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('123', 'A') RETURNING id")
    site_id = cur.fetchone()["id"]; conn.commit(); cur.close(); conn.close()
    record_site_fetch_success(site_id, tmp_db)
    rows = get_sites_with_health(tmp_db)
    assert rows[0]["stale"] is False


def test_record_forecast_points_ignores_duplicates(tmp_db):
    from datetime import datetime, timezone, timedelta
    from db.models import record_forecast_points, get_forecast_points
    issued = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    pts = [{"valid_at": issued + timedelta(hours=24), "stage": 15.0}]
    assert record_forecast_points("abcd1", issued, pts, tmp_db) == 1
    assert record_forecast_points("abcd1", issued, pts, tmp_db) == 0
    assert len(get_forecast_points("abcd1", tmp_db)) == 1


def test_gauge_quality_roundtrip(tmp_db):
    from db.models import get_or_create_noaa_gauge, set_gauge_quality, get_gauge_quality
    get_or_create_noaa_gauge("abcd1", "Test", 10.0, 12.0, 14.0, 16.0, tmp_db)
    assert get_gauge_quality("abcd1", tmp_db) is None
    set_gauge_quality("abcd1", "B", "MAE 0.8 ft at 24 h", tmp_db)
    q = get_gauge_quality("abcd1", tmp_db)
    assert q["grade"] == "B"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test pytest tests/db/test_models.py -q`
Expected: FAIL with `ImportError: cannot import name 'link_page_site'`.

- [ ] **Step 3: Add the schema, migrations, settings, and helpers to `db/models.py`**

Add `MIGRATION_STATEMENTS` as a module-level list containing the `ALTER TABLE` / `CREATE INDEX` statements above, and execute it in `init_db` after `SCHEMA_STATEMENTS`. Add the three new `CREATE TABLE` statements to `SCHEMA_STATEMENTS`. Add the seven new keys to `DEFAULT_SETTINGS`. Implement each helper listed under **Produces** following the existing connection-per-call pattern.

For `get_sites_with_health`, compute staleness in SQL against the setting:

```python
def get_sites_with_health(db_path=None):
    """Return every site with last_success_at/last_error plus a `stale` flag."""
    stale_hours = float(get_setting("site_stale_hours", db_path, default="6"))
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT *,
                      (last_success_at IS NULL
                       OR last_success_at < NOW() - (%s * INTERVAL '1 hour')) AS stale
               FROM sites ORDER BY id""",
            (stale_hours,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Add the new tables to `_DROP_ALL` in `tests/conftest.py`**

`page_sites`, `noaa_observations`, and `gauge_forecasts` must be listed alongside the existing tables, before `user_pages` and `sites` so the CASCADE ordering stays readable.

- [ ] **Step 5: Pin `requirements.txt` and add waitress**

Replace the unpinned list with exact pins matching what the production image currently runs, keeping the existing comment headings:

```
dataretrieval==1.2.0
pandas==3.0.3
numpy==2.4.6
requests==2.34.2
matplotlib==3.11.1
psycopg2-binary==2.9.12
Flask==3.1.3
waitress==3.0.2
python-telegram-bot==22.8
twilio==9.10.9
pytest==9.1.1
pytest-mock==3.15.1
```

- [ ] **Step 6: Run the full suite**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build test`
Expected: all previously-passing tests plus the six new ones pass.

- [ ] **Step 7: Commit**

```bash
git add db/models.py tests/conftest.py tests/db/test_models.py requirements.txt
git commit -m "feat(db): page-site links, site health, forecast archive, gauge quality; pin deps"
```

---

## Task 2: Authentication and webhook signature verification

**Files:**
- Create: `web/auth.py`, `web/health.py`, `tests/web/test_auth.py`, `tests/web/test_health.py`
- Modify: `web/app.py`, `web/routes.py`, `tests/web/test_app.py`, `tests/web/test_dashboard.py`, `tests/web/test_sites.py`, `tests/web/test_settings.py`, `tests/web/test_subscribers.py`, `tests/web/test_broadcast.py`, `tests/web/test_pages.py`

**Interfaces:**
- Consumes: `db.models.get_setting`.
- Produces:

```python
# web/auth.py
PUBLIC_ENDPOINTS: frozenset[str]   # endpoint names that never require auth
init_auth(app) -> None             # installs the before_request guard
admin_credentials() -> tuple[str, str] | None
    # (username, password_hash) from env, or None when unconfigured

# web/health.py
register_health(app) -> None
    # adds GET /healthz; endpoint name "healthz"
    # reads app.config["THREAD_REGISTRY"], a dict[str, threading.Thread] set by main.py
    # 200 {"status":"ok","threads":{...}} when every thread is alive
    # 503 {"status":"degraded","threads":{...}} when any is dead
    # 200 {"status":"ok","threads":{}} when the registry is absent (test/portal-only use)
```

**Auth design.** HTTP Basic. Credentials come from the environment, never the database:
- `ADMIN_USERNAME` (default `admin`)
- `ADMIN_PASSWORD_HASH` — a `werkzeug.security.generate_password_hash` output; preferred.
- `ADMIN_PASSWORD` — plaintext fallback, hashed at startup.

If neither password variable is set, protected routes must return **503** with the body `Admin credentials are not configured. Set ADMIN_PASSWORD_HASH or ADMIN_PASSWORD.` — fail closed, never fall open.

Public endpoints (no auth): `healthz`, `static`, `webhook_twilio`, `webhook_twilio_status`, `webhook_facebook`, `page_new`, `page_view`, `page_edit`, `page_add_gauge`, `page_search_gauges`, `page_remove_gauge`, `page_subscribe`, `page_unsubscribe`. Every other endpoint requires Basic auth. The `/edit/<edit_token>` family is authenticated by its unguessable token, which is the existing design.

- [ ] **Step 1: Write the failing auth tests**

Create `tests/web/test_auth.py`:

```python
import base64
import pytest
from db.models import init_db
from web.app import create_app


def _client(monkeypatch, tmp_db, password="secret"):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    if password is None:
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    else:
        monkeypatch.setenv("ADMIN_PASSWORD", password)
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    return app.test_client()


def _auth(user="admin", pw="secret"):
    raw = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def test_dashboard_requires_auth(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/").status_code == 401


def test_dashboard_allows_correct_credentials(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/", headers=_auth()).status_code == 200


def test_dashboard_rejects_wrong_password(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/", headers=_auth(pw="wrong")).status_code == 401


def test_settings_requires_auth(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/settings/channels").status_code == 401


def test_broadcast_requires_auth(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.post("/broadcast", data={"message": "hi"}).status_code == 401


def test_unconfigured_password_returns_503_not_open_access(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db, password=None)
    resp = c.get("/")
    assert resp.status_code == 503
    assert b"ADMIN_PASSWORD" in resp.data


def test_public_page_view_does_not_require_auth(monkeypatch, tmp_db):
    from db.models import create_user_page
    c = _client(monkeypatch, tmp_db)
    public, _ = create_user_page("Test", tmp_db)
    assert c.get(f"/view/{public}").status_code == 200


def test_healthz_does_not_require_auth(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/healthz").status_code == 200
```

- [ ] **Step 2: Run and confirm they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test pytest tests/web/test_auth.py -q`
Expected: FAIL — `/` returns 200 without credentials.

- [ ] **Step 3: Implement `web/auth.py` and `web/health.py`, wire both into `web/app.py`**

`init_auth(app)` registers a `before_request` handler that returns `None` for endpoints in `PUBLIC_ENDPOINTS` (and for `request.endpoint is None`), returns the 503 body when `admin_credentials()` is `None`, and otherwise checks `request.authorization` with `werkzeug.security.check_password_hash`, returning `Response(status=401, headers={"WWW-Authenticate": 'Basic realm="River Monitor"'})` on failure.

Call `init_auth(app)` and `register_health(app)` from `create_app` **after** `register_routes(app)`.

- [ ] **Step 4: Run the auth tests and confirm they pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test pytest tests/web/test_auth.py tests/web/test_health.py -q`
Expected: PASS.

- [ ] **Step 5: Write failing tests for webhook signature verification**

Add to `tests/web/test_auth.py`:

```python
def test_facebook_webhook_rejects_unsigned_post(monkeypatch, tmp_db):
    from db.models import set_setting
    c = _client(monkeypatch, tmp_db)
    set_setting("facebook_app_secret", "s3cret", tmp_db)
    resp = c.post("/webhook/facebook", json={"entry": []})
    assert resp.status_code == 403


def test_facebook_webhook_accepts_valid_signature(monkeypatch, tmp_db):
    import hmac, hashlib, json
    from db.models import set_setting
    c = _client(monkeypatch, tmp_db)
    set_setting("facebook_app_secret", "s3cret", tmp_db)
    body = json.dumps({"entry": []}).encode()
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    resp = c.post("/webhook/facebook", data=body,
                  headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
    assert resp.status_code == 200


def test_twilio_webhook_rejects_unsigned_post(monkeypatch, tmp_db):
    from db.models import set_setting
    c = _client(monkeypatch, tmp_db)
    set_setting("twilio_auth_token", "tok", tmp_db)
    resp = c.post("/webhook/twilio", data={"From": "+15025551234", "Body": "JOIN"})
    assert resp.status_code == 403
```

- [ ] **Step 6: Implement signature verification in `web/routes.py`**

Facebook POST: read `request.get_data()` once, compute `hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()`, compare to the `X-Hub-Signature-256` header with `hmac.compare_digest`. When `facebook_app_secret` is empty, return 403 — fail closed. Parse the JSON from the raw body you already read.

Twilio POST (both `/webhook/twilio` and `/webhook/twilio/status`): use `twilio.request_validator.RequestValidator(auth_token)` against `request.url` and `request.form.to_dict()`, reading the `X-Twilio-Signature` header. Empty `twilio_auth_token` returns 403.

- [ ] **Step 7: Update the existing webhook tests to send valid signatures**

The existing Twilio and Facebook webhook tests in `tests/web/` post unsigned payloads and will now get 403. Update them to compute a real signature — for Twilio use `RequestValidator(token).compute_signature(url, params)` so the test exercises the production validator rather than bypassing it.

- [ ] **Step 8: Update every other existing web test to authenticate**

`tests/web/test_app.py`, `test_dashboard.py`, `test_sites.py`, `test_settings.py`, `test_subscribers.py`, `test_broadcast.py`, and the admin-route tests in `test_pages.py` all hit protected endpoints. Add the `ADMIN_PASSWORD` env var and an `Authorization` header to their shared `client` fixtures. Prefer extending each file's existing fixture over rewriting the tests.

- [ ] **Step 9: Run the full suite**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test`
Expected: all tests pass.

- [ ] **Step 10: Commit**

```bash
git add web/ tests/web/
git commit -m "feat(web): basic auth on portal, verify Facebook and Twilio webhook signatures, add /healthz"
```

---

## Task 3: Telegram adapter — hot token reload and per-page subscription

**Files:**
- Modify: `monitor/adapters/telegram.py`
- Test: `tests/monitor/test_telegram_adapter.py` (create)

**Interfaces:**
- Consumes: `db.models.get_setting`, `db.models.get_page_by_public_token`, `db.models.add_page_subscriber`, `db.models.set_page_subscriber_status`.
- Produces: `TelegramAdapter` keeps `channel = "telegram"` and `send(chat_id, message) -> bool`.

**Two problems to fix.**

1. `run()` reads `telegram_bot_token` once and returns permanently when it is empty, so entering the token in the Settings page does nothing until the container restarts. Restructure `run()` into a supervisor loop: wait for a token (re-checking every `TOKEN_POLL_SECONDS = 30`), start the bot, and watch for the token changing or `stop_event` being set — then stop the bot and loop again. Use a small watcher thread that calls `self._app.stop_running()` (available in python-telegram-bot 22.8) when `get_setting("telegram_bot_token")` no longer equals the running token or when `stop_event` is set. Keep `run_polling(stop_signals=None)`, which returns once the loop stops. Wrap the whole body in try/except so a crash logs and retries rather than killing the thread.

2. A Telegram user has no way to subscribe to a *specific* page, so they cannot receive the per-gauge alerts Task 4 routes. Add a `/subscribe <page_token>` form:
   - `/subscribe` with no argument keeps the existing behavior — global subscriber, used only for manual broadcasts — and the reply must say so: `"✓ Subscribed to broadcast announcements. To get alerts for a specific river page, send /subscribe <page code> — find the code on your page's web address."`
   - `/subscribe <token>` looks the page up with `get_page_by_public_token`. Unknown or inactive token replies `"I couldn't find a page with that code. Check the link your page owner sent you."`. On success it calls `add_page_subscriber(page["id"], "telegram", chat_id, display_name)` and replies `"✓ You'll get alerts for {page_name}."`.
   - `/unsubscribe` with no argument deactivates the global subscriber **and** sets every one of that chat's `page_subscribers` rows to `unsubscribed`; `/unsubscribe <token>` unsubscribes from just that page.
   - Add `/mypages` listing the pages this chat is subscribed to, so users can check.

Keep the blocking psycopg2 calls out of the event loop by running each handler's DB work through `asyncio.to_thread`.

- [ ] **Step 1: Write failing tests**

Create `tests/monitor/test_telegram_adapter.py`. Test the DB-facing logic directly rather than standing up a real bot — extract the handler bodies into synchronous helpers so they are testable:

```python
def test_subscribe_with_page_token_adds_page_subscriber(tmp_db):
    from db.models import create_user_page, get_page_by_public_token, get_active_page_subscribers
    from monitor.adapters.telegram import subscribe_chat_to_page
    public, _ = create_user_page("Ohio at Louisville", tmp_db)
    ok, reply = subscribe_chat_to_page("555", "Ann", public, tmp_db)
    assert ok is True
    page = get_page_by_public_token(public, tmp_db)
    assert [s["channel_id"] for s in get_active_page_subscribers(page["id"], tmp_db)] == ["555"]
    assert "Ohio at Louisville" in reply


def test_subscribe_with_unknown_token_reports_failure(tmp_db):
    from monitor.adapters.telegram import subscribe_chat_to_page
    ok, reply = subscribe_chat_to_page("555", "Ann", "not-a-real-token", tmp_db)
    assert ok is False
    assert "couldn't find" in reply


def test_unsubscribe_all_clears_every_page(tmp_db):
    from db.models import create_user_page, get_page_by_public_token, get_active_page_subscribers
    from monitor.adapters.telegram import subscribe_chat_to_page, unsubscribe_chat_everywhere
    public, _ = create_user_page("P", tmp_db)
    subscribe_chat_to_page("555", "Ann", public, tmp_db)
    unsubscribe_chat_everywhere("555", tmp_db)
    page = get_page_by_public_token(public, tmp_db)
    assert get_active_page_subscribers(page["id"], tmp_db) == []
```

Also test the supervisor's token-change detection without network I/O:

```python
def test_adapter_waits_for_token_instead_of_exiting(tmp_db, monkeypatch):
    import threading
    from monitor.adapters.telegram import TelegramAdapter
    adapter = TelegramAdapter(db_path=tmp_db)
    adapter.TOKEN_POLL_SECONDS = 0.01
    started = []
    monkeypatch.setattr(adapter, "_run_bot", lambda token: started.append(token))
    t = threading.Thread(target=adapter.run, daemon=True)
    t.start()
    from db.models import set_setting
    set_setting("telegram_bot_token", "abc123", tmp_db)
    for _ in range(200):
        if started:
            break
        import time; time.sleep(0.01)
    adapter.stop_event.set()
    t.join(timeout=2)
    assert started == ["abc123"]
```

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test pytest tests/monitor/test_telegram_adapter.py -q`
Expected: FAIL — `cannot import name 'subscribe_chat_to_page'`.

- [ ] **Step 3: Implement the helpers and the supervisor loop**

Extract `_run_bot(token)` so the supervisor is testable without network access. Signature of the new module-level helpers:

```python
def subscribe_chat_to_page(chat_id, display_name, page_token, db_path=None) -> tuple[bool, str]
def unsubscribe_chat_from_page(chat_id, page_token, db_path=None) -> tuple[bool, str]
def unsubscribe_chat_everywhere(chat_id, db_path=None) -> str
def list_chat_pages(chat_id, db_path=None) -> list[str]
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test pytest tests/monitor/test_telegram_adapter.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add monitor/adapters/telegram.py tests/monitor/test_telegram_adapter.py
git commit -m "feat(telegram): hot-reload bot token, add per-page /subscribe and /mypages"
```

---

## Task 4: Polling and dispatch — per-site routing, rise/fall alerts, thread survival

**Files:**
- Create: `monitor/trend.py`, `tests/monitor/test_trend.py`
- Modify: `monitor/polling.py`, `monitor/dispatcher.py`, `monitor/noaa_polling.py`, `tests/monitor/test_polling.py`, `tests/monitor/test_dispatcher.py`, `tests/monitor/test_noaa_polling.py`

**Interfaces:**
- Consumes: everything under Task 1 **Produces**.
- Produces: queue message shapes that Task 7 and the dispatcher rely on.

```python
# monitor/trend.py
compute_trend(site_id, window_hours, db_path=None) -> dict | None
    # None when fewer than 2 readings inside the window.
    # dict keys: direction ("RISING"|"FALLING"|"STEADY"), delta (float,
    #   signed end-minus-start), start_value, end_value, hours (float actual span)

exceeds_trend_threshold(delta, parameter_code, start_value, db_path=None) -> bool
    # 00065 (stage, ft): abs(delta) >= rate_change_threshold_ft
    # anything else (e.g. 00060 discharge, cfs): abs(delta) >= start_value *
    #   rate_change_threshold_pct / 100, and False when start_value <= 0

trend_alert_due(site_id, db_path=None) -> bool
    # False when a 'trend' notification for this site was logged less than
    # rate_change_min_interval_hours ago. Compare using
    # "sent_at" ordered by id DESC — never order by the text timestamp.
```

Five changes:

**4a. Suppress the first-reading false alert.** `detect_transition(None, new)` currently returns `(None, "NORMAL")`, which sends every newly added site an alert reading `Condition changed: None → NORMAL`. Both transitions ever recorded in production are this bug. Return `None` when `previous_severity` is `None`. Update the existing test in `tests/monitor/test_polling.py` that asserts the old behavior, and add one asserting the new behavior.

**4b. Route USGS alerts to the right people.** In `dispatcher.py`, the `transition` and `reminder` branches call `get_active_subscribers()` — every subscriber, no site filter. Change both to `get_page_subscribers_for_site(site_id, self.db_path)`. Leave the `broadcast` branch on `get_active_subscribers()`; that table is now broadcast-only. Pass `subscriber_id=None` to `log_notification` for page subscribers, matching what the `noaa_transition` branch already does.

**4c. Say which way the river is moving.** `fetch_and_evaluate_site` must add `"direction"` to the transition dict by comparing the new reading to the previous `site_conditions` row: `"RISING"`, `"FALLING"`, or `"STEADY"` (equal values). Add a `get_previous_reading(site_id, db_path)` helper in `polling.py` returning the last row's `current_value` or `None`; direction is `"STEADY"` when there is no previous reading. `format_transition_message` gains a leading direction line:

```
📈 RISING — Ohio River at Louisville (#03294500)
Condition changed: NORMAL → HIGH
Current level: 24.30 ft (91.2th percentile)
```

Use 📈 for RISING, 📉 for FALLING, and ➡️ for STEADY.

**4d. Alert on rate of change, not just band crossings.** This is the headline gap: in 12 days of production data the stage moved several feet and the percentile ranged 58–82 without a single alert, because nothing crossed the 10/90 band. After recording each condition, `fetch_and_evaluate_site` computes `compute_trend(...)`; when `exceeds_trend_threshold(...)` and `trend_alert_due(...)` are both true it returns a second message. Change `PollingThread._poll` to accept a list of messages per site rather than one transition. Add a `"trend"` type to the dispatcher, routed like `transition` (per-site page subscribers) and logged with `trigger_type="trend"`:

```python
def format_trend_message(data):
    """Build the alert text for a river rising or falling quickly."""
    arrow = "📈" if data["direction"] == "RISING" else "📉"
    verb = "risen" if data["direction"] == "RISING" else "fallen"
    return (
        f"{arrow} River {data['direction'].title()}: {data['station_name']} (#{data['site_number']})\n"
        f"Has {verb} {abs(data['delta']):.2f} {data['unit']} in the last {data['hours']:.1f} hours\n"
        f"Now {data['end_value']:.2f} {data['unit']} (was {data['start_value']:.2f})"
    )
```

**4e. Stop threads dying silently.** `PollingThread.run` calls `_poll()` and `get_setting(...)` with no exception handling, so one Postgres blip kills polling permanently while the container still reports healthy. Wrap the loop body of `PollingThread.run` and `NoaaPollingThread.run` so any exception is logged and the loop continues with a fallback interval:

```python
def run(self):
    """Poll on each iteration then wait poll_interval_minutes, looping until stop_event is set."""
    logger.info("PollingThread started")
    while not self.stop_event.is_set():
        interval = self.DEFAULT_INTERVAL_MINUTES
        try:
            self._poll()
            interval = int(get_setting("poll_interval_minutes", self.db_path, default="15"))
        except Exception:
            logger.exception("Polling cycle failed — retrying next interval")
        self.stop_event.wait(timeout=interval * 60)
    logger.info("PollingThread stopped")
```

Set `DEFAULT_INTERVAL_MINUTES = 15` on both classes. Do the same for `NotificationDispatcher.run` around `run_once()`.

Also record site health: call `record_site_fetch_success(site_id)` after a successful evaluation and `record_site_fetch_error(site_id, str(exc))` in the `except` block of `fetch_and_evaluate_site`. Replace the three silent `return None` branches (`polling.py:118`, `:122`, `:135` — no matching parameter column, non-numeric or negative value, empty history) with a `logger.warning` **and** a `record_site_fetch_error` call describing which check failed. Two of the four production sites have returned zero readings for 12 days with no user-visible signal; this is what surfaces them.

In `noaa_polling.py`, call `record_noaa_observation(lid, stage)` on every successful fetch so Task 5 can score forecasts against real observations.

- [ ] **Step 1: Write the failing tests**

Cover, at minimum: `compute_trend` returns `None` with one reading; returns `RISING` with an increasing pair; `exceeds_trend_threshold` respects the ft threshold for `00065` and the percentage threshold for `00060`; `trend_alert_due` is False right after a logged trend notification; `detect_transition(None, "NORMAL")` returns `None`; the dispatcher sends a transition only to subscribers of a page linked to that site and **not** to a subscriber of an unrelated page; `format_trend_message` contains the direction and delta; and `PollingThread.run` survives `_poll` raising (patch `_poll` to raise once, assert the thread is still alive and `_poll` was called again).

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test pytest tests/monitor -q`
Expected: FAIL on the new modules and assertions.

- [ ] **Step 3: Implement 4a–4e**

- [ ] **Step 4: Run the monitor tests**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test pytest tests/monitor -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add monitor/ tests/monitor/
git commit -m "feat(monitor): per-site alert routing, rise/fall alerts, direction wording, resilient poll loops"
```

---

## Task 5: NOAA forecasts and gauge flood-prediction grading

**Files:**
- Create: `monitor/gauge_quality.py`, `monitor/forecast_polling.py`, `tests/monitor/test_gauge_quality.py`, `tests/monitor/test_forecast_polling.py`
- Modify: `monitor/noaa_client.py`, `tests/monitor/test_noaa_client.py`

**Interfaces:**
- Consumes: Task 1 helpers `record_forecast_points`, `get_forecast_points`, `get_noaa_observations`, `set_gauge_quality`, `get_gauge_quality`, `get_all_noaa_gauges`.
- Produces:

```python
# monitor/noaa_client.py
fetch_forecast(lid, timeout=TIMEOUT) -> dict | None
    # GET /gauges/{lid}/stageflow/forecast
    # {"issued_at": datetime, "points": [{"valid_at": datetime, "stage": float}, ...]}
    # None on any error or when the gauge publishes no forecast.

# monitor/gauge_quality.py
MIN_SAMPLES = 10
score_gauge(gauge, db_path=None) -> dict
    # gauge: a row from get_all_noaa_gauges()
    # returns {"grade": str, "headline": str, "detail": str,
    #          "mae_24h": float|None, "mae_48h": float|None, "mae_72h": float|None,
    #          "samples": int, "has_flood_categories": bool,
    #          "publishes_forecast": bool, "reporting": bool}
forecast_error_samples(lid, horizon_hours, tolerance_minutes=90, db_path=None) -> list[float]
    # absolute error in feet between each archived forecast point at
    # ~horizon_hours after issue and the observation nearest its valid_at
score_all_gauges(db_path=None) -> int   # scores every gauge, persists, returns count
```

**Why this task exists.** The user asked for "a judgement of if the chosen river gauge makes reasonable flood predictions." Today the only related signal is a `noaa_has_flood` boolean that renders a "🌊 NOAA flood forecast" badge — that says thresholds exist, not that predictions are any good. Nothing in the codebase fetches a forecast at all.

**Grading rubric** — implement exactly this, and document it in the module docstring so the portal can explain the grade honestly:

1. `has_flood_categories` — false means the gauge cannot predict flooding at all. Grade `"F"`, headline `"Not usable for flood warning"`, detail `"NOAA publishes no flood-stage thresholds for this gauge, so it cannot tell you when flooding starts."`
2. `publishes_forecast` false → grade `"D"`, headline `"Observation only"`, detail `"This gauge reports current levels but NOAA publishes no forecast for it, so you get no advance warning."`
3. `reporting` false (no observation in the last 24 h) → grade `"F"`, headline `"Not reporting"`, detail `"No readings in the last 24 hours."`
4. Fewer than `MIN_SAMPLES` matched forecast/observation pairs → grade `"Unrated"`, headline `"Collecting accuracy data"`, detail `"Has flood thresholds and publishes forecasts. Accuracy grade needs {MIN_SAMPLES} matched forecasts; {samples} so far."`
5. Otherwise grade on `mae_24h`: `< 0.5 ft` → `"A"`; `< 1.0` → `"B"`; `< 2.0` → `"C"`; else `"D"`. Headline `"Reliable flood predictions"` / `"Good"` / `"Fair"` / `"Unreliable"` respectively. Detail states the numbers: `"24-hour forecasts are off by {mae_24h:.2f} ft on average ({samples} forecasts checked)."` Append the 48 h and 72 h figures when available.

`forecast_error_samples` pairs each archived forecast point whose `valid_at - issued_at` is within ±90 minutes of `horizon_hours` against the observation closest to its `valid_at` (also within ±90 minutes), and returns `abs(predicted_stage - observed_stage)`. Skip points with no nearby observation.

`ForecastPollingThread` in `monitor/forecast_polling.py` mirrors the existing thread pattern: it sleeps `forecast_poll_hours` between passes, and on each pass calls `fetch_forecast` for every gauge, stores the points with `record_forecast_points`, then runs `score_all_gauges`. Wrap the loop body in try/except exactly as Task 4e specifies, with `DEFAULT_INTERVAL_HOURS = 6`. It takes `(db_path=None, stop_event=None)` — it does not need the notification queue.

- [ ] **Step 1: Write the failing tests**

Mock `requests.get` for `fetch_forecast` — no live network calls in tests. Cover both NWPS response shapes the existing `fetch_gauge_metadata` handles. For grading, insert synthetic forecast points and observations into `tmp_db` and assert each rubric branch, including: no flood categories → `"F"`; forecasts but only 3 samples → `"Unrated"` with the sample count in the detail; 12 samples averaging 0.3 ft error → `"A"`; 12 samples averaging 1.5 ft → `"C"`.

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test pytest tests/monitor/test_gauge_quality.py tests/monitor/test_forecast_polling.py -q`
Expected: FAIL — modules do not exist.

- [ ] **Step 3: Implement `fetch_forecast`, `gauge_quality.py`, and `forecast_polling.py`**

- [ ] **Step 4: Run the tests**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test pytest tests/monitor -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add monitor/ tests/monitor/
git commit -m "feat(noaa): archive forecasts and grade each gauge's flood-prediction accuracy"
```

---

## Task 6: Portal UI — site health, page-site linking, gauge grades

**Files:**
- Modify: `web/routes.py`, `web/templates/sites.html`, `web/templates/page_edit.html`, `web/templates/page_view.html`, `web/templates/base.html`, `tests/web/test_sites.py`, `tests/web/test_pages.py`

**Interfaces:**
- Consumes: `get_sites_with_health`, `link_page_site`, `unlink_page_site`, `get_page_sites`, `get_gauge_quality`, and the auth helpers from Task 2 (`PUBLIC_ENDPOINTS` must gain any new public endpoint added here).

Runs **after** Tasks 2 and 5 land, because it edits `web/routes.py` (Task 2's file) and reads Task 5's grades.

Three additions:

1. **Surface dead sites.** `/sites` currently lists sites with no indication that two of the four production sites have returned nothing for 12 days. Switch the route to `get_sites_with_health()` and render a red `⚠ Not reporting` badge with the `last_error` text as a tooltip for any row where `stale` is true.

2. **Let a page own USGS sites, not just NOAA gauges.** Task 4 routes USGS alerts through `page_sites`, so the editor needs to manage that link or the routing has no data. Add to `page_edit.html` a "River gauges (USGS)" section listing `get_page_sites(page["id"])` with a remove button, plus an add form. Add two routes, both public (token-authenticated) and both added to `PUBLIC_ENDPOINTS`:
   - `POST /edit/<edit_token>/sites/add` — endpoint `page_add_site`; takes `site_number`, validates with the existing `validate_usgs_site`, inserts into `sites` if new (`ON CONFLICT (site_number) DO NOTHING`), then `link_page_site`.
   - `POST /edit/<edit_token>/sites/remove` — endpoint `page_remove_site`; takes `site_id`, calls `unlink_page_site`.

3. **Show the flood-prediction judgement.** On `page_view.html` and `page_edit.html`, render each NOAA gauge's grade from `get_gauge_quality(gauge["lid"])`: the letter as a colored badge (A/B green, C amber, D/F red, Unrated grey), the headline in bold, and the detail as plain sentence text underneath. When `get_gauge_quality` returns `None`, show `Unrated — not yet assessed`. This is the deliverable for "give the user a judgement of if the chosen river gauge makes reasonable flood predictions" — the wording must be plain English, not a bare number.

- [ ] **Step 1: Write failing route tests**

Assert `/sites` renders the not-reporting badge for a site with no `last_success_at`; that `POST /edit/<token>/sites/add` links the site and it appears in `get_page_sites`; that `POST /edit/<token>/sites/remove` unlinks it; that both work **without** an `Authorization` header; and that `/view/<public_token>` shows the gauge's grade headline.

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test pytest tests/web -q`

- [ ] **Step 3: Implement the routes and templates**

- [ ] **Step 4: Run the full suite**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/ tests/web/
git commit -m "feat(web): site health badges, page-site linking, gauge flood-prediction grades"
```

---

## Task 7: Runtime, supervision, and deployment

**Files:**
- Modify: `main.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`, `CLAUDE.md`

Runs last — it wires together every thread the other tasks produced.

1. **Serve with waitress.** Replace `flask_app.run(host="0.0.0.0", port=5743, ...)` with `waitress.serve(flask_app, host="0.0.0.0", port=5743, threads=8)`. The Werkzeug development server is not built for continuous production use.

2. **Register threads for health reporting.** Build `thread_registry = {t.name: t for t in [...]}` covering polling, NOAA polling, forecast polling, scheduler, dispatcher, and any adapter threads, and pass it into `create_app(..., thread_registry=thread_registry)` so `/healthz` can report liveness. Add the `thread_registry` parameter to `create_app` — **coordinate with Task 2**, which owns `web/app.py`; if that parameter does not exist yet, add it here and set `app.config["THREAD_REGISTRY"]`.

3. **Start the forecast thread.** Instantiate `ForecastPollingThread(stop_event=stop_event)` from Task 5 and start it with the others.

4. **Supervise instead of blocking forever.** `main()` currently ends at `stop_event.wait()`, so if every worker thread dies the process still sits there and Docker's `restart: unless-stopped` never fires. Replace it with a loop that wakes every 60 seconds, logs any dead thread by name, and — when a non-adapter worker thread has died — logs `CRITICAL` and returns, letting the container exit so Docker restarts it:

```python
CRITICAL_THREADS = ("PollingThread", "NoaaPollingThread", "ForecastPollingThread",
                    "SchedulerThread", "NotificationDispatcher", "WebThread")

while not stop_event.wait(timeout=60):
    dead = [name for name, t in thread_registry.items() if not t.is_alive()]
    if any(name in CRITICAL_THREADS for name in dead):
        logger.critical("Worker thread(s) died: %s — exiting so Docker restarts us", dead)
        return
    if dead:
        logger.warning("Non-critical thread(s) not running: %s", dead)
```

5. **Add a container healthcheck.** `python:3.11-slim` has no `curl`, so use Python. In `docker-compose.yml` under `app`:

```yaml
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5743/healthz', timeout=5).status == 200 else 1)"]
      interval: 60s
      timeout: 10s
      start_period: 30s
      retries: 3
```

Add the new admin credential variables to the `environment:` block, failing loudly like the existing entries:

```yaml
      - ADMIN_USERNAME=${ADMIN_USERNAME:-admin}
      - ADMIN_PASSWORD_HASH=${ADMIN_PASSWORD_HASH:?set ADMIN_PASSWORD_HASH in .env}
```

6. **Run as a non-root user.** In the `Dockerfile`, after `RUN mkdir -p logs`, add:

```dockerfile
RUN useradd --create-home --uid 10001 river && chown -R river:river /app
USER river
```

7. **Document it.** Add to `.env.example` the `ADMIN_USERNAME` and `ADMIN_PASSWORD_HASH` variables with a comment showing how to generate the hash:

```
# Portal login. Generate the hash with:
#   docker compose run --rm app python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('your-password'))"
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=
```

Update `README.md` and `CLAUDE.md` to describe: portal authentication, the `/healthz` endpoint, the new `page_sites` routing model, `/subscribe <page code>` on Telegram, rate-of-change alerts and their settings, the gauge grading rubric, and the new `ForecastPollingThread` in the module layout and architecture sections.

- [ ] **Step 1: Update `main.py`, then verify the module imports cleanly**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test python -c "import main; print('ok')"`
Expected: `ok`

- [ ] **Step 2: Update `Dockerfile`, `docker-compose.yml`, and `.env.example`**

- [ ] **Step 3: Run the full test suite**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build test`
Expected: all tests pass.

- [ ] **Step 4: Build and start the real stack, then verify it end to end**

Set `ADMIN_PASSWORD_HASH` in `.env` first. Then:

Run: `docker compose up -d --build`
Run: `docker compose ps`
Expected: the `app` container reports `(healthy)` within about 90 seconds.

Run: `docker exec my_river_level-app-1 python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:5743/healthz', timeout=5).read().decode())"`
Expected: JSON with `"status": "ok"` and every thread listed as alive.

Run: `docker logs --tail 40 my_river_level-app-1`
Expected: every thread logs "started"; no tracebacks.

- [ ] **Step 5: Update the docs and commit**

```bash
git add main.py Dockerfile docker-compose.yml .env.example README.md CLAUDE.md
git commit -m "feat(runtime): waitress, thread supervision, container healthcheck, non-root user, docs"
```

---

## Definition of Done

- [ ] `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build test` passes with every pre-existing test still present.
- [ ] `docker compose ps` shows the app `(healthy)`.
- [ ] An unauthenticated `GET /` returns 401; `GET /healthz` returns 200.
- [ ] An unsigned `POST /webhook/facebook` returns 403.
- [ ] A USGS transition reaches only subscribers of pages linked to that site.
- [ ] A stage change beyond the configured threshold produces a RISING or FALLING alert.
- [ ] Adding a brand-new site produces no `None → NORMAL` alert.
- [ ] `/sites` flags the two production sites that have never reported.
- [ ] `/view/<token>` shows a plain-English grade for each NOAA gauge's flood-prediction quality.
