# River Monitor — Windows Service & Notification System Design

**Date:** 2026-03-01
**Status:** Approved

## Overview

Transform the existing CLI river monitor into a persistent Windows background service with a localhost admin portal and a multi-channel subscriber notification system. Subscribers register by messaging a bot; the service detects USGS condition state transitions and sends reminders on severity-based cadences.

---

## Architecture

Single `pywin32` Windows service process (`RiverMonitorService`) containing four threads:

```
RiverMonitorService (pywin32 Windows Service)
│
├── PollingThread          — wakes every 15 min, fetches USGS data,
│                            computes percentiles, detects state transitions,
│                            queues notifications
│
├── SchedulerThread        — wakes every 5 min, checks if any active
│                            severity level is due a reminder based on cadence
│                            (daily for LOW/HIGH, every 4h for SEVERE),
│                            queues notifications
│
├── NotificationDispatcher — worker thread pulling from a queue, routes each
│                            message to the right channel adapter
│   ├── TelegramAdapter    — python-telegram-bot, handles /start opt-in
│   ├── WhatsAppAdapter    — Twilio WhatsApp API
│   ├── SMSAdapter         — Twilio SMS API
│   └── FacebookAdapter    — Meta Messenger API (page token)
│
└── WebThread              — Flask app on localhost:8080
    ├── Dashboard          — current site conditions, recent alerts
    ├── Subscribers        — view/add/remove, test messages
    ├── Sites              — add/remove/toggle monitored sites
    ├── Settings           — all config including channel credentials
    └── Broadcast          — manual compose and send to all
```

SQLite (`db/river_monitor.db`) is the single shared state store for all threads.

---

## Notification Triggers

- **State transitions** — any change in severity level (e.g. NORMAL → HIGH, HIGH → SEVERE HIGH, SEVERE HIGH → NORMAL) triggers an immediate notification to all active subscribers.
- **Reminders** — periodic messages sent even when severity has not changed:
  - NORMAL: no reminders
  - LOW / HIGH: once per day
  - SEVERE LOW / SEVERE HIGH: every 4 hours

---

## Notification Channels

| Channel | Opt-in mechanism | Library | Outbound API |
|---|---|---|---|
| Telegram | `/start` then `/subscribe` to bot | `python-telegram-bot` | Bot API |
| WhatsApp | Text `JOIN` to Twilio WhatsApp number | `twilio` | Twilio REST |
| SMS | Text `JOIN` to Twilio number | `twilio` | Twilio REST |
| Facebook Messenger | Message Facebook Page | `requests` | Meta Send API |

Inbound WhatsApp, SMS, and Facebook messages arrive as webhooks to the Flask web thread (`/webhook/twilio`, `/webhook/facebook`). Telegram uses long-polling inside the NotificationDispatcher thread.

All channel credentials (tokens, SIDs, page tokens) are stored in the `settings` table and editable from the portal — no `.env` or config file editing after initial setup.

---

## Data Model

```sql
-- Monitored USGS sites
sites
  id, site_number, station_name, parameter_code, active, added_at

-- App-wide settings (replaces config.py scalars)
settings
  key, value

-- Condition readings (one row per poll; latest row = current state)
site_conditions
  id, site_id, checked_at, current_value, unit, percentile, severity

-- Registered subscribers
subscribers
  id, display_name, channel, channel_id, opted_in_at, active

-- Notification log
notifications
  id, subscriber_id, site_id, sent_at, channel, message_text,
  trigger_type, success, error_msg

-- Pending bot opt-ins
pending_registrations
  id, channel, channel_id, started_at
```

State transitions are detected by comparing the newly inserted `site_conditions` row's severity to the previous row's severity for that site.

---

## Web Portal (localhost:8080)

Five pages, Bootstrap via CDN (no build step):

- **Dashboard (`/`)** — live site conditions table with color-coded severity badges, recent alerts feed. Auto-refreshes every 60 seconds.
- **Subscribers (`/subscribers`)** — subscriber list with channel, ID, opt-in date; inline remove; manual add form; per-subscriber test message button.
- **Sites (`/sites`)** — monitored site list with active/inactive toggle; add by USGS number or via bounding-box search (reuses `RiverMonitor.find_nearby_sites()`); per-site parameter code editable inline.
- **Settings (`/settings`)** — form for all settings table values: poll interval, percentile thresholds, reminder cadences, channel credentials. Saving triggers live adapter reload without service restart.
- **Broadcast (`/broadcast`)** — free-text compose, channel filter checkboxes, send button. Logged to notifications table with `trigger_type = manual`.

No authentication — localhost only.

---

## Windows Service

`pywin32` service class `RiverMonitorService` inherits `win32serviceutil.ServiceFramework`.

```bash
python service.py install   # register as Windows service
python service.py start
python service.py stop
python service.py remove
```

Runs as `LocalSystem`. Logs to Windows Event Log and `logs/river_monitor.log` (rotating).

---

## Project Structure

```
My_River_level/
├── service.py              # pywin32 service entry point
├── monitor/
│   ├── polling.py          # PollingThread
│   ├── scheduler.py        # SchedulerThread
│   ├── dispatcher.py       # NotificationDispatcher + queue
│   └── adapters/
│       ├── telegram.py
│       ├── whatsapp.py
│       ├── sms.py
│       └── facebook.py
├── web/
│   ├── app.py              # Flask app factory
│   └── templates/          # Jinja2 templates
├── db/
│   ├── models.py           # SQLite schema + helpers
│   └── river_monitor.db    # created on first run
├── river_monitor.py        # retained for standalone CLI use
├── config.py / Bushmans.py # retained for CLI backwards compat
├── requirements.txt
└── CLAUDE.md
```

---

## Migration Path

On first service start, if a `config.py` exists its values are read once and seeded into the `settings` and `sites` tables. After that the database is the sole source of truth.

---

## New Dependencies

```
flask
pywin32
python-telegram-bot
twilio
apscheduler
```

`requests` is already present. `dataretrieval`, `pandas`, `numpy` carry over unchanged.
