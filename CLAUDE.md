# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

The service runs on `http://localhost:5743`. Logs go to `logs/river_monitor.log` (rotating, 5 MB, 3 backups).

## Shell commands

Never chain or pipe bash commands. Run one command at a time. Do not use `&&`, `||`, `|`, or `;` to combine commands in a single Bash call.

## Architecture

The primary entry point is `main.py`, which starts three daemon threads sharing a single `notification_queue`:

1. **Polling thread** (`monitor/polling.py`) — fetches USGS data on a configurable interval and enqueues notifications when thresholds are crossed.
2. **Scheduler thread** (`monitor/scheduler.py`) — enforces reminder intervals so alerts aren't sent too frequently for persistent conditions.
3. **Dispatcher thread** (`monitor/dispatcher.py`) — reads from the queue and routes messages to notification adapters.
4. **Flask web server** (`web/app.py`, `web/routes.py`) — runs in the main thread; provides the management portal and handles webhooks.

### Module layout

```
main.py                 — Entry point; starts all threads
Dockerfile              — Container image definition
docker-compose.yml      — Multi-container orchestration (app + PostgreSQL)
monitor/
  polling.py            — USGS data fetch loop; uses dataretrieval nwis.get_iv / get_dv
  scheduler.py          — Throttles repeat alerts; tracks last-notified timestamps
  dispatcher.py         — Dequeues notifications and calls adapters
  site_validation.py    — Validates USGS site numbers against the API
  phone_utils.py        — Phone number normalization for Twilio channels
  adapters/
    telegram.py         — Telegram Bot API
    sms.py              — Twilio SMS
    whatsapp.py         — Twilio WhatsApp
    facebook.py         — Facebook Messenger webhook
web/
  app.py                — Flask app factory
  routes.py             — Dashboard, Sites, Subscribers, Settings, Broadcast, webhooks
db/
  models.py             — PostgreSQL schema, init, and all DB helper functions
tests/
  conftest.py           — Shared pytest fixtures (tmp_db)
  db/                   — Tests for models
  monitor/              — Tests for polling, scheduling, dispatching, phone utils, site validation
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

### Webhook endpoints

- `POST /webhook/twilio` — Twilio SMS/WhatsApp status callbacks and inbound messages (`JOIN`/`STOP`)
- `GET|POST /webhook/facebook` — Facebook Messenger verify and inbound messages
