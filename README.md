# River Level Extreme Conditions Monitor

A Python system that monitors river gauges, detects extreme water conditions
(floods and droughts), and delivers alerts to subscribers via multiple
notification channels. It runs as a set of background threads with a Flask web
management portal, packaged as a Docker container backed by PostgreSQL.

## Quick start

1. `cp .env.example .env` and fill `DATABASE_URL`, `TEST_DATABASE_URL`, `FLASK_SECRET_KEY`.
2. `uv run --with werkzeug python set_admin_password.py` — sets the portal password in `.env`.
3. `docker compose up -d --build` (needs a reachable PostgreSQL; see the tutorial).
4. Open `http://localhost:5743`, log in, and paste your Telegram bot token on **Settings → Notification Channels**.
5. Set **public base URL** on **Settings → Monitoring**, then send `/start` to your bot.

## Documentation

| | |
|---|---|
| [Start here](docs/README.md) | Index of the whole manual |
| [First run](docs/tutorials/first-run.md) | Clean machine to first alert |
| [How-to guides](docs/README.md#how-to-guides) | Deploy, upgrade, back up, connect channels, diagnose |
| [Reference](docs/README.md#reference) | Every setting, variable, table, route, command, thread |
| [Explanation](docs/README.md#explanation) | How classification, routing, discovery and retirement work |

Developers: see [CLAUDE.md](CLAUDE.md).

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
  condition badges, each with its own subscriber list — alerts are routed to a
  page's subscribers only for the gauges that page watches
- Alerts on **rate of change**, not just threshold crossings: a river that
  rises or falls quickly triggers a 📈/📉 alert even while it stays inside the
  normal percentile band
- Grades each NOAA gauge's **flood-prediction quality** by archiving its
  published forecasts and scoring them against what the river actually did

## Resources

- [USGS Water Data for the Nation](https://waterdata.usgs.gov/)
- [dataretrieval Documentation](https://github.com/DOI-USGS/dataRetrieval)
- [NOAA National Water Prediction Service (NWPS)](https://water.noaa.gov/)
- [National Water Dashboard](https://dashboard.waterdata.usgs.gov/)
- USGS data support: gs-w_waterdata_support@usgs.gov
