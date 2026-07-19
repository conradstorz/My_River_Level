# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Database

This project uses a shared, always-on PostgreSQL server that lives in a
separate `shared-postgres` project (one server hosts every project's databases;
River Monitor uses `rivermonitor` and `river_test`, owned by role `river`).
Start it once and it stays up:

```bash
# In the shared-postgres project directory
docker compose up -d
```

River's containers reach it over the external `shared-db` Docker network at
hostname `postgres` — no published ports, so it works even when the Docker
daemon is remote from the CLI machine.

### Secrets

Credentials live in a gitignored `.env` (compose loads it automatically);
nothing sensitive is committed. Copy the template and fill in real values:

```bash
cp .env.example .env
```

`DATABASE_URL` / `TEST_DATABASE_URL` must use the same `river` password as
`RIVER_DB_PASSWORD` in the shared-postgres project's `.env`. Compose fails
loudly (`:?`) if any required value is missing.

## Commands

```bash
# Install dependencies (for local development / running tests)
pip install -r requirements.txt

# Run tests against a local PostgreSQL (see .env.example for TEST_DATABASE_URL)
pytest

# Run the full test suite in Docker (requires the shared-postgres server to be
# up). Builds a test image that includes tests/ and runs pytest inside the
# Docker network against the shared server. Works even when the Docker daemon
# is remote. The river_test database is auto-created if missing.
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test

# Run a specific test file
pytest tests/monitor/test_polling.py

# Run the app with Docker (requires the shared-postgres server to be up)
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

The service runs on `http://localhost:5743`. Logs go to `logs/river_monitor.log` (rotating, 5 MB, 3 backups).

## Shell commands

Never chain or pipe bash commands. Run one command at a time. Do not use `&&`, `||`, `|`, or `;` to combine commands in a single Bash call.

## Architecture

The primary entry point is `main.py`, which starts several daemon threads sharing a single `notification_queue`:

1. **USGS polling thread** (`monitor/polling.py`) — fetches USGS data on a configurable interval and enqueues notifications when percentile thresholds are crossed.
2. **NOAA polling thread** (`monitor/noaa_polling.py`) — fetches NOAA NWPS gauge stages and enqueues notifications when the flood category (Action/Minor/Moderate/Major) changes.
3. **Scheduler thread** (`monitor/scheduler.py`) — enforces reminder intervals so alerts aren't sent too frequently for persistent conditions.
4. **Dispatcher thread** (`monitor/dispatcher.py`) — reads from the queue and routes messages to notification adapters.
5. **Flask web server** (`web/app.py`, `web/routes.py`) — runs in its own thread; provides the management portal and handles webhooks.

### Module layout

```
main.py                 — Entry point; starts all threads
Dockerfile              — Container image definition
docker-compose.yml      — Multi-container orchestration (app + PostgreSQL)
monitor/
  polling.py            — USGS data fetch loop; uses dataretrieval nwis.get_iv / get_dv
  noaa_polling.py       — NOAA NWPS fetch loop; flood-category classification
  noaa_client.py        — NOAA NWPS API client and severity mapping
  scheduler.py          — Throttles repeat alerts; tracks last-notified timestamps
  dispatcher.py         — Dequeues notifications and calls adapters
  site_search.py        — Ranked USGS gauge search by name (Monitoring Locations OGC API)
  site_validation.py    — Validates USGS site numbers against the API
  phone_utils.py        — Phone number normalization for Twilio channels
  adapters/
    telegram.py         — Telegram Bot API
    sms.py              — Twilio SMS
    whatsapp.py         — Twilio WhatsApp
    facebook.py         — Facebook Messenger webhook
web/
  app.py                — Flask app factory
  routes.py             — Dashboard, Sites, Subscribers, Settings, Broadcast,
                          user landing pages (/pages, /view, /edit, /admin/pages), webhooks
db/
  models.py             — PostgreSQL schema, init, and all DB helper functions
tests/
  conftest.py           — Shared pytest fixtures (tmp_db)
  db/                   — Tests for models
  monitor/              — Tests for polling, scheduling, dispatching, phone utils, site validation, site search
  web/                  — Tests for all Flask routes
```

### Data flow

1. `polling.py` calls `nwis.get_iv()` (interval values, last 7 days) and `nwis.get_dv()` (daily values since `historical_start_year`) for each active site.
2. Percentiles are computed with numpy by ranking the current value against the historical distribution.
3. `classify_condition()` maps percentile to severity: SEVERE_LOW / LOW / NORMAL / HIGH / SEVERE_HIGH.
4. When a threshold is crossed (and the scheduler allows it), a message dict is pushed onto `notification_queue`.
5. `dispatcher.py` pops from the queue and calls the relevant adapter(s).

### Configuration and database

All runtime settings are stored in PostgreSQL (connection via `DATABASE_URL` env var). There are no config files at runtime.

Key settings stored in the DB: `poll_interval_minutes`, `low_percentile`, `high_percentile`, `very_low_percentile`, `very_high_percentile`, `reminder_low_high_hours`, `reminder_severe_hours`, `historical_start_year`, `search_radius_miles`, and per-channel credentials (Telegram token, Twilio SID/token/numbers, Facebook tokens).

### USGS API

Uses the `dataretrieval` package (`nwis` module):

| Call | Purpose |
|---|---|
| `nwis.get_iv(sites, parameterCd, start, end)` | Real-time interval values |
| `nwis.get_dv(sites, parameterCd, start, end)` | Historical daily values |
| `nwis.what_sites(bBox, parameterCd, siteStatus)` | Discover gauges in bounding box |
| `nwis.get_info(sites)` | Site metadata |

Column names from `get_iv` use pattern `"00060"` or `"00060_00000"`; from `get_dv` use `"00060_Mean"`. Filtering is done with `startswith(param_code)` or `param_code in col`.

Gauge search by name uses a separate service — the USGS Monitoring Locations
OGC API (`https://api.waterdata.usgs.gov/ogcapi/v0/collections/monitoring-locations/items`),
queried via `requests` in `monitor/site_search.py` (the `nwis` site service has
no substring name search).

### NOAA API

`monitor/noaa_client.py` calls the NOAA National Water Prediction Service
(NWPS) v1 API (`https://api.water.noaa.gov/nwps/v1`) via `requests`:

| Call | Purpose |
|---|---|
| `GET /gauges/{lid}` | Station name, current stage, and flood-category thresholds (action / minor / moderate / major) |

`classify_noaa_condition()` maps the current stage to Normal / Action / Minor /
Moderate / Major. NOAA gauges are stored in the `noaa_gauges` table and attached
to user landing pages (`page_noaa_gauges`); `noaa_polling.py` polls each gauge
once regardless of how many pages reference it.

### Webhook endpoints

- `POST /webhook/twilio` — Twilio SMS/WhatsApp status callbacks and inbound messages (`JOIN`/`STOP`)
- `GET|POST /webhook/facebook` — Facebook Messenger verify and inbound messages
