# River Level Extreme Conditions Monitor

A Python system that monitors river gauges, detects extreme water conditions
(floods and droughts), and delivers alerts to subscribers via multiple
notification channels. It runs as a set of background threads with a Flask web
management portal, packaged as a Docker container backed by PostgreSQL.

## Features

- Monitors **USGS** stream gauges in real time and classifies conditions by
  ranking the current value against a historical percentile baseline
- Monitors **NOAA** (NWPS) river gauges against official flood-category
  thresholds (Action / Minor / Moderate / Major)
- Sends alerts via Telegram, SMS, WhatsApp, and Facebook Messenger
- Web portal for managing sites, subscribers, settings, and manual broadcasts
- Add gauges by USGS number **or** by searching gauge names — a ranked keyword
  search tolerant of word order and typos
- Shareable per-user **landing pages** showing NOAA hydrographs and live
  condition badges, each with its own subscriber list

## Architecture Overview

The entry point is `main.py`, which starts a set of daemon threads that share a
single `notification_queue`, plus the Flask web server:

```
main.py                 — Entry point; starts all threads
├── monitor/
│   ├── polling.py       — USGS data fetch loop (percentile classification)
│   ├── noaa_polling.py  — NOAA NWPS fetch loop (flood-category classification)
│   ├── noaa_client.py   — NOAA NWPS API client and severity mapping
│   ├── scheduler.py     — Throttles repeat alerts (thresholds, reminders)
│   ├── dispatcher.py    — Routes notifications from the queue to adapters
│   ├── site_search.py   — Ranked USGS gauge search by name (OGC API)
│   ├── site_validation.py — Validates USGS site numbers against the API
│   ├── phone_utils.py   — Phone number normalization for Twilio channels
│   └── adapters/
│       ├── telegram.py
│       ├── sms.py       (Twilio)
│       ├── whatsapp.py  (Twilio)
│       └── facebook.py
├── web/
│   ├── app.py          — Flask app factory
│   └── routes.py       — Dashboard, Sites, Subscribers, Settings, Broadcast,
│                         landing pages, and webhooks
└── db/
    └── models.py       — PostgreSQL schema, init, and all DB helper functions
```

The web portal can also inject broadcast messages directly into the queue.

### Data flow

1. `polling.py` fetches USGS interval and daily values for each active site;
   percentiles are computed with numpy and mapped to a severity
   (SEVERE_LOW → SEVERE_HIGH).
2. `noaa_polling.py` fetches the current stage for each NOAA gauge and maps it
   to a flood category (Normal → Major) using thresholds from the NWPS API.
3. When a condition crosses a threshold (and the scheduler allows it), a
   message is pushed onto `notification_queue`.
4. `dispatcher.py` pops from the queue and calls the relevant adapter(s).

## Installation

### Prerequisites

- Docker (with Compose) — the supported way to run the service
- The shared **`shared-postgres`** server running (River connects to it over
  the external `shared-db` Docker network; it does not run its own database)
- Python 3.11 — only needed for running tests or the app outside Docker

### Setup

```bash
git clone git@github.com:conradstorz/My_River_Level.git
cd My_River_Level

# Create your environment file and fill in real values
cp .env.example .env
```

`.env` holds the PostgreSQL connection URLs, the Flask secret key, and any
notification-channel credentials. It is gitignored — nothing sensitive is
committed. The `river` DB password must match `RIVER_DB_PASSWORD` in the
`shared-postgres` project's `.env`.

## Running

### With Docker (recommended)

First make sure the shared PostgreSQL server is up (in the `shared-postgres`
project: `docker compose up -d`). Then, from this repo:

```bash
docker compose up --build
```

The portal is served at `http://localhost:5743`. Stop with:

```bash
docker compose down
```

### Production deployment

Build and push to the GitHub Container Registry, then pull on the server:

```bash
# Local
docker build -t ghcr.io/<your-org>/river-monitor:latest .
docker push ghcr.io/<your-org>/river-monitor:latest

# On the server
docker compose pull
docker compose up -d
```

### Outside Docker (development)

```bash
python -m venv venv
venv\Scripts\activate          # Windows;  source venv/bin/activate on Unix
pip install -r requirements.txt

# Point at a reachable PostgreSQL and a Flask secret
export DATABASE_URL=postgresql://river:<password>@localhost:5432/rivermonitor
export FLASK_SECRET_KEY=<random-string>

python main.py
```

### Logs

Logs rotate at 5 MB (3 backups) and are written to `logs/river_monitor.log`.
In Docker they persist in the `app_logs` named volume.

## Web Portal

Open `http://localhost:5743` after starting the service.

| Page | URL | Purpose |
|------|-----|---------|
| Dashboard | `/` | Live site conditions and recent notification history |
| Sites | `/sites` | Add gauges (by USGS number or name search), toggle, or remove |
| Subscribers | `/subscribers` | Manage alert recipients by channel |
| Settings | `/settings` | Polling interval, percentile thresholds, and channel credentials |
| Broadcast | `/broadcast` | Send a manual message to all (or selected) channels |
| Landing pages | `/pages/new`, `/admin/pages` | Create and manage shareable NOAA gauge pages |

## Configuration

All runtime settings are stored in PostgreSQL and editable via the Settings
page. Notification-channel credentials can be supplied through `.env` or the
Settings page. Defaults:

| Setting | Default | Description |
|---------|---------|-------------|
| `poll_interval_minutes` | 15 | How often to fetch USGS data |
| `low_percentile` | 10 | Below-normal threshold |
| `high_percentile` | 90 | Above-normal threshold |
| `very_low_percentile` | 5 | Severe drought threshold |
| `very_high_percentile` | 95 | Severe flood threshold |
| `reminder_low_high_hours` | 24 | Re-alert interval for LOW/HIGH conditions |
| `reminder_severe_hours` | 4 | Re-alert interval for SEVERE conditions |
| `historical_start_year` | 1980 | Oldest year used for baseline statistics |
| `search_radius_miles` | 25 | Radius for automatic gauge discovery |

## Notification Channels

Configure credentials on the Settings page or in `.env`.

### Telegram

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token.
2. Set **Telegram Bot Token** (Settings page or `TELEGRAM_BOT_TOKEN` in `.env`).
3. Users subscribe by sending `/start` to your bot (or add them manually via the Subscribers page).

### SMS / WhatsApp (Twilio)

1. Create a [Twilio](https://www.twilio.com/) account and provision a number.
2. Enter **Account SID**, **Auth Token**, and phone numbers (Settings page or the `TWILIO_*` variables in `.env`).
3. Point your Twilio SMS/WhatsApp webhook to `http://<your-host>:5743/webhook/twilio`.
4. Users subscribe by texting `JOIN` and unsubscribe with `STOP`.

### Facebook Messenger

1. Create a Facebook App with Messenger enabled and generate a Page Access Token.
2. Enter **Page Token** and a **Verify Token** of your choice (Settings page or the `FACEBOOK_*` variables in `.env`).
3. Set your webhook URL to `http://<your-host>:5743/webhook/facebook`.
4. Users subscribe by messaging `JOIN` to your page.

## USGS Gauge Setup

On the **Sites** page you can add a gauge two ways:

- **Search by name** — type a gauge name (e.g. `ohio river at louisville`) and
  pick from the ranked results. The search accepts keywords in any order,
  ignores punctuation and case, expands full state names to the USGS
  abbreviation, and tolerates typos (`lousville` still finds `LOUISVILLE`).
- **By USGS number** — enter an 8-digit USGS site number (e.g. `03293000`) and
  an optional station name. Find gauge numbers at
  [waterdata.usgs.gov](https://waterdata.usgs.gov/).

### Parameter codes

- `00060` — Discharge (streamflow) in cubic feet per second (cfs)
- `00065` — Gage height in feet

### USGS condition classifications

| Severity | Percentile | Description |
|----------|-----------|-------------|
| SEVERE HIGH | ≥ 95th | Severe flood conditions |
| HIGH | ≥ 90th | Above-normal flow, flood risk |
| NORMAL | 10th–90th | Normal conditions |
| LOW | ≤ 10th | Below-normal flow, drought |
| SEVERE LOW | ≤ 5th | Severe drought conditions |

## NOAA Gauges and Landing Pages

The service also monitors NOAA river gauges (via the NWPS API) against official
flood-category thresholds. NOAA gauges are managed through **landing pages**:

1. Create a page at `/pages/new`. You receive a public view URL
   (`/view/<token>`) to share and a secret edit URL (`/edit/<token>`).
2. On the edit page, add NOAA gauges by LID (e.g. `MLUK2`). Station name and
   flood thresholds are fetched from NOAA automatically. Gauges are shared
   across pages, so a gauge referenced by two pages is still polled once.
3. Visitors to the public page see each gauge's hydrograph and a live condition
   badge, and can subscribe to that page's alerts.
4. Administrators can enable/disable pages at `/admin/pages`.

### NOAA condition classifications

| Severity | Meaning |
|----------|---------|
| Major | At or above the major flood stage |
| Moderate | At or above the moderate flood stage |
| Minor | At or above the minor flood stage |
| Action | At or above the action stage |
| Normal | Below the action stage |

## Testing

Run the full suite in Docker against the shared PostgreSQL server (the
`river_test` database is auto-created if missing). The test image bakes in the
source and tests, so pass `--build` to pick up changes:

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest -q
```

Or run locally against a reachable PostgreSQL (see `TEST_DATABASE_URL` in
`.env.example`):

```bash
pytest
```

Tests cover the database models, web routes, and monitor components (polling,
NOAA client, scheduling, dispatching, phone utilities, site validation, and
gauge search).

## Resources

- [USGS Water Data for the Nation](https://waterdata.usgs.gov/)
- [dataretrieval Documentation](https://github.com/DOI-USGS/dataRetrieval)
- [NOAA National Water Prediction Service (NWPS)](https://water.noaa.gov/)
- [National Water Dashboard](https://dashboard.waterdata.usgs.gov/)
- USGS data support: gs-w_waterdata_support@usgs.gov
