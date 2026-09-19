# Alerts

Every item type that travels on the notification queue, who produces it, and who receives it.

| type | produced by | payload keys | recipients | message format |
|---|---|---|---|---|
| `broadcast` | `web: /broadcast` | `message`, `channels` | The global `subscribers` table (`active=1`), filtered to the channels selected on the form. | Sent verbatim — the admin's free-text message, unformatted. |
| `direct` | `web: /pin/<edit_token>/save` | `channel`, `channel_id`, `message` | One chat: the page's `owner_chat_id`, when the saved pin belongs to a Telegram chat. | Sent verbatim — an f-string built by the producing route; see below. |
| `noaa_transition` | `NoaaPollingThread` | `gauge_id`, `lid`, `station_name`, `previous_severity`, `new_severity`, `current_stage` | Active `page_subscribers` of every live page (`user_pages.active=1 AND status='active'`) linked to the gauge via `page_noaa_gauges`, filtered by `alert_allowed()` (always allowed for this type) — see [`../explanation/alert-routing-and-sensitivity.md`](../explanation/alert-routing-and-sensitivity.md). | `format_noaa_transition_message`; see below. |
| `reminder` | `SchedulerThread` | `site_id`, `site_number`, `station_name`, `severity`, `current_value`, `unit`, `percentile` | Active `page_subscribers` of every live page linked to the site via `page_sites`, filtered by `alert_allowed()` — see [`../explanation/alert-routing-and-sensitivity.md`](../explanation/alert-routing-and-sensitivity.md). | `format_reminder_message`; see below. |
| `transition` | `PollingThread` | `site_id`, `site_number`, `station_name`, `previous_severity`, `new_severity`, `current_value`, `unit`, `percentile`, `direction` | Active `page_subscribers` of every live page linked to the site, filtered by `alert_allowed()` (checks both `new_severity` and `previous_severity`, so an all-clear back to NORMAL still reaches a `floods`-only page that heard the SEVERE HIGH). | `format_transition_message`; see below. |
| `trend` | `PollingThread` | `site_id`, `site_number`, `station_name`, `unit`, `direction`, `delta`, `start_value`, `end_value`, `hours` | Active `page_subscribers` of every live page linked to the site, filtered by `alert_allowed()` (trend alerts reach only pages dialed to `all`). | `format_trend_message`; see below. |

## Message formats

`transition` (`format_transition_message`):

```
📈 RISING — Willamette River at Portland (#14211720)
Condition changed: NORMAL → HIGH
Current level: 45230.00 cfs (91.3th percentile)
```

`trend` (`format_trend_message`):

```
📈 River Rising: Willamette River at Portland (#14211720)
Has risen 2.30 ft in the last 4.0 hours
Now 12.80 ft (was 10.50)
```

`reminder` (`format_reminder_message`):

```
🔔 River Level Reminder: Willamette River at Portland (#14211720)
Current condition: HIGH
Level: 45230.00 cfs (91.3th percentile)
```

`noaa_transition` (`format_noaa_transition_message`):

```
⚠️ River Level Change: Cedar River at Renton (MLUK2)
Condition changed: Action → Minor
Current stage: 12.40 ft
View: https://water.noaa.gov/gauges/mluk2
```

`direct` (built inline in `web/routes.py: pin_save`, not by a `format_*` function):

```
✓ You're set up for Cedar River. You'll hear about:
• Cedar River at Renton
• Willamette River at Portland (#14211720)

Send /settings any time to change gauges or sensitivity.
```

`broadcast` has no format function — the message queued by `web: /broadcast` is delivered verbatim.

## Severity vocabulary

| system | values (low to high) |
|---|---|
| NOAA (`noaa_gauges.severity`) | Unknown / Normal / Action / Minor / Moderate / Major |
| USGS (`site_conditions.severity`) | SEVERE LOW / LOW / NORMAL / HIGH / SEVERE HIGH / UNKNOWN |

Verified against commit 20c729b
