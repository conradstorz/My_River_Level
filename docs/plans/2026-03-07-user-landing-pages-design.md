# User Landing Pages with NOAA Gauge Monitoring

**Date:** 2026-03-07
**Status:** Approved

## Overview

Add per-user shareable landing pages that display NOAA hydrograph images and live condition badges for one or more river gauges. The service polls NOAA gauges on a schedule and sends notifications to per-page subscribers when conditions change significantly. The landing page is an optional visual layer on top of the existing notification system.

## Data Model

Four new tables added to `db/river_monitor.db`. Existing tables are untouched.

### `user_pages`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `public_token` | TEXT UNIQUE | UUID — used in shareable view URL |
| `edit_token` | TEXT UNIQUE | UUID — used in secret edit URL |
| `page_name` | TEXT | Display name chosen by owner |
| `created_at` | TEXT | datetime |
| `active` | INTEGER | 1=visible, 0=disabled by admin |

### `noaa_gauges`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `lid` | TEXT UNIQUE | NOAA LID e.g. `MLUK2` |
| `station_name` | TEXT | Fetched from NOAA API on add |
| `current_stage` | REAL | Latest polled value (ft) |
| `action_stage` | REAL | From NOAA gauge metadata |
| `minor_flood_stage` | REAL | |
| `moderate_flood_stage` | REAL | |
| `major_flood_stage` | REAL | |
| `severity` | TEXT | Normal / Action / Minor / Moderate / Major |
| `last_polled_at` | TEXT | datetime |

Shared across all pages — if two pages reference MLUK2, it is polled once.

### `page_noaa_gauges`
| Column | Type | Notes |
|---|---|---|
| `page_id` | INTEGER FK → user_pages | |
| `noaa_gauge_id` | INTEGER FK → noaa_gauges | |

### `page_subscribers`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `page_id` | INTEGER FK → user_pages | |
| `channel` | TEXT | telegram / sms / whatsapp / facebook |
| `channel_id` | TEXT | phone number, PSID, chat ID, etc. |
| `display_name` | TEXT | |
| `status` | TEXT | active / paused / unsubscribed |
| `opted_in_at` | TEXT | datetime |

## Service Changes

### NOAA Polling
- A new polling loop runs alongside the existing USGS loop (same thread or companion thread), governed by the existing `poll_interval_minutes` setting.
- For each unique `noaa_gauges` row: `GET https://api.water.noaa.gov/nwps/v1/gauges/{lid}/stageflow/observed`
- Severity derived from `current_stage` vs stored flood thresholds:
  - `current_stage >= major_flood_stage` → Major
  - `current_stage >= moderate_flood_stage` → Moderate
  - `current_stage >= minor_flood_stage` → Minor
  - `current_stage >= action_stage` → Action
  - otherwise → Normal
- Gauge metadata (station name, flood thresholds) fetched once on first add via `GET /nwps/v1/gauges/{lid}` and refreshed daily.

### Notifications
- On severity change, a notification is enqueued for every `active` `page_subscriber` on every page that includes the changed gauge.
- `paused` subscribers are skipped but retained.
- `unsubscribed` subscribers are never messaged.
- Uses the existing dispatcher and channel adapters — no new adapter code needed.

## Web Routes

### Public
| Method | Route | Purpose |
|---|---|---|
| GET | `/pages/new` | Form: enter a page name |
| POST | `/pages/new` | Create page; show public + edit URLs once |
| GET | `/view/<public_token>` | Landing page: NOAA PNGs + severity badges |

### Edit (secret token)
| Method | Route | Purpose |
|---|---|---|
| GET | `/edit/<edit_token>` | Manage page: gauges, name, subscribers |
| POST | `/edit/<edit_token>/gauges/add` | Add NOAA gauge by LID; validated against API |
| POST | `/edit/<edit_token>/gauges/remove` | Remove gauge from page |
| POST | `/edit/<edit_token>/subscribe` | Add self as notification subscriber |
| POST | `/edit/<edit_token>/unsubscribe` | Pause or unsubscribe self |

### Admin (existing portal, new section)
| Method | Route | Purpose |
|---|---|---|
| GET | `/admin/pages` | List all pages with gauge/subscriber counts |
| POST | `/admin/pages/<page_id>/toggle` | Enable / disable a page |

## User Flows

### Creating a page
1. Visit `/pages/new`, enter a page name.
2. System generates two UUIDs and stores the page.
3. A one-time screen shows both URLs clearly labeled ("Share this" / "Keep this private").
4. Owner bookmarks the edit URL and shares the view URL.

### Adding a gauge
1. Owner visits edit URL, enters a NOAA LID (e.g. `MLUK2`).
2. System validates against NOAA API — confirms gauge exists, fetches flood thresholds.
3. Gauge appears on the page immediately.

### Subscribing to notifications
1. Visitor clicks "Get Alerts" on the public view page.
2. Directed to a subscribe form (can be on the public page or a linked form).
3. Chooses channel and enters channel ID; added as `active` to `page_subscribers`.

### Self-service subscription management
- Subscribers manage status via existing channel mechanisms:
  - Twilio: text `PAUSE` / `RESUME` / `STOP` to the service number
  - Telegram: send `/pause`, `/resume`, `/stop` to the bot
- Existing webhook handlers extended to check `page_subscribers` in addition to global `subscribers`.

### Admin moderation
- Admin views all pages at `/admin/pages`.
- Disabling a page (`active=0`) causes `/view/<token>` to return 404.
- Admin does not edit page content — only enables/disables.

## NOAA API Reference

| Call | Purpose |
|---|---|
| `GET /nwps/v1/gauges/{lid}` | Metadata: name, flood thresholds, photos |
| `GET /nwps/v1/gauges/{lid}/stageflow/observed` | Time-series of observed stage + flow |
| Hydrograph image | `https://water.noaa.gov/resources/hydrographs/{lid}_hg.png` |

## Out of Scope

- Authentication / passwords (low-friction design, no PII/financial data)
- Per-gauge subscriber filtering (all page subscribers get all gauge alerts for that page)
- USGS percentile analysis for NOAA gauges (NOAA flood thresholds used instead)
- Editing another user's page
