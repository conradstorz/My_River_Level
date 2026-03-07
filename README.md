# River Level Extreme Conditions Monitor

A Python system that monitors USGS stream gauges, detects extreme water conditions (floods and droughts), and delivers alerts to subscribers via multiple notification channels. It runs as a Windows service with a web management portal.

## Features

- Monitors USGS stream gauges in real time
- Calculates statistical percentiles against historical baselines to classify conditions
- Sends alerts via Telegram, SMS, WhatsApp, and Facebook Messenger
- Web portal for managing sites, subscribers, settings, and manual broadcasts
- Runs as a Windows background service
- Auto-migrates legacy `config.py` files to the SQLite database on first start

## Architecture Overview

```
service.py          — Windows service entry point; orchestrates all threads
├── monitor/
│   ├── polling.py      — Fetches USGS data on a configurable interval
│   ├── scheduler.py    — Decides when to trigger notifications (thresholds, reminders)
│   ├── dispatcher.py   — Routes notifications from the queue to adapters
│   └── adapters/
│       ├── telegram.py
│       ├── sms.py       (Twilio)
│       ├── whatsapp.py  (Twilio)
│       └── facebook.py
├── web/
│   ├── app.py          — Flask app factory
│   └── routes.py       — Dashboard, Sites, Subscribers, Settings, Broadcast
├── db/
│   ├── models.py       — SQLite schema, init, and helper functions
│   └── migration.py    — Imports config.py into the database on first run
├── river_monitor.py    — Standalone CLI monitor (original script)
└── setup_wizard.py     — Interactive CLI for finding and selecting USGS gauges
```

All threads share a single `notification_queue`. The web portal can also inject broadcast messages directly into the queue.

## Installation

### Prerequisites

- Python 3.8+
- Windows (for the service; `python service.py debug` works on any OS)
- Administrator privileges (for service install/start/stop)

### Setup

```bash
git clone git@github.com:conradstorz/My_River_Level.git
cd My_River_Level

# Windows quick setup
setup.bat

# Or manually
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Running

### As a Windows Service (recommended for production)

Run these commands as Administrator:

```bash
python service.py install
python service.py start
```

The service starts automatically on boot and runs at `http://localhost:5743`.

```bash
python service.py stop
python service.py remove
```

### In Debug Mode (development / any OS)

```bash
python service.py debug
```

Runs all threads in the foreground. Press Ctrl+C to stop.

### Desktop Shortcut (Windows)

Create a one-click launcher on your Desktop:

```bash
python create_shortcut.py
```

Double-clicking **River Monitor** on the Desktop will:
- Open the portal immediately if the service is already running
- Start the service and then open the portal if it is stopped
- Launch in debug mode (new console window) if the service is not installed

You can also run the launcher directly at any time:

```bash
pythonw launch.py   # silent (no console)
python launch.py    # with console output
```

### Logs

Logs rotate at 5 MB (3 backups) and are written to `logs/river_monitor.log`.

## Web Portal

Open `http://localhost:5743` after starting the service.

| Page | URL | Purpose |
|------|-----|---------|
| Dashboard | `/` | Live site conditions and recent notification history |
| Sites | `/sites` | Add, toggle active/inactive, or remove monitored gauges |
| Subscribers | `/subscribers` | Manage alert recipients by channel |
| Settings | `/settings` | Polling interval, percentile thresholds, and channel credentials |
| Broadcast | `/broadcast` | Send a manual message to all (or selected) channels |

## Configuration

All settings are stored in the SQLite database at `db/river_monitor.db` and editable via the Settings page. Defaults:

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

### Legacy config migration

If a `config.py` file exists from a previous installation, it is automatically imported into the database on first start. The file is not deleted.

## Notification Channels

Configure credentials on the Settings page or directly in the database.

### Telegram

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token.
2. Paste it into **Telegram Bot Token** on the Settings page.
3. Users subscribe by sending `/start` to your bot (or add them manually via the Subscribers page).

### SMS / WhatsApp (Twilio)

1. Create a [Twilio](https://www.twilio.com/) account and provision a number.
2. Enter **Account SID**, **Auth Token**, and phone numbers on the Settings page.
3. Point your Twilio SMS/WhatsApp webhook to `http://<your-host>:5743/webhook/twilio`.
4. Users subscribe by texting `JOIN` and unsubscribe with `STOP`.

### Facebook Messenger

1. Create a Facebook App with Messenger enabled and generate a Page Access Token.
2. Enter **Page Token** and a **Verify Token** of your choice on the Settings page.
3. Set your webhook URL to `http://<your-host>:5743/webhook/facebook`.
4. Users subscribe by messaging `JOIN` to your page.

## USGS Gauge Setup

### Using the setup wizard (recommended)

```bash
python setup_wizard.py
```

The wizard geocodes an address, finds nearby active gauges, lets you preview recent data, and adds your selections to the database.

### Manual addition

On the **Sites** page, enter an 8-digit USGS site number (e.g. `03293000`) and optional station name. Find gauge numbers at [waterdata.usgs.gov](https://waterdata.usgs.gov/).

### Parameter codes

- `00060` — Discharge (streamflow) in cubic feet per second (cfs)
- `00065` — Gage height in feet

## Condition Classifications

| Severity | Percentile | Description |
|----------|-----------|-------------|
| SEVERE HIGH | ≥ 95th | Severe flood conditions |
| HIGH | ≥ 90th | Above-normal flow, flood risk |
| NORMAL | 10th–90th | Normal conditions |
| LOW | ≤ 10th | Below-normal flow, drought |
| SEVERE LOW | ≤ 5th | Severe drought conditions |

## Standalone CLI Monitor

The original CLI script still works independently:

```bash
# Run with default config
python river_monitor.py

# Run with a named config file
python river_monitor.py --config Bushmans

# List available configs
python river_monitor.py --list-configs
```

## Testing

```bash
pytest
```

Tests cover database models and migration, web routes, and monitor components (polling, scheduling, dispatching).

## Resources

- [USGS Water Data for the Nation](https://waterdata.usgs.gov/)
- [dataretrieval Documentation](https://github.com/DOI-USGS/dataRetrieval)
- [Find Monitoring Locations](https://waterdata.usgs.gov/nwis/rt)
- [National Water Dashboard](https://dashboard.waterdata.usgs.gov/)
- USGS data support: gs-w_waterdata_support@usgs.gov
