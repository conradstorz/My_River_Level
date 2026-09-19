# Database

Every table `init_db` creates, then the additive migrations it applies on every start; `init_db` is safe to re-run.

## `sites`

| column | type | meaning | written by |
|---|---|---|---|
| `id` | SERIAL PRIMARY KEY | | `init_db` |
| `site_number` | TEXT NOT NULL UNIQUE | USGS site number. | `web: /sites/add`, `web: /edit/<edit_token>/sites/add`, `db/models.py: save_pin` |
| `station_name` | TEXT NOT NULL DEFAULT '' | | same as `site_number` |
| `parameter_code` | TEXT NOT NULL DEFAULT '00060' | USGS parameter code (`00060` discharge in cfs, `00065` gauge height in ft). | same as `site_number` |
| `active` | INTEGER NOT NULL DEFAULT 1 | 0/1 flag; `PollingThread` only polls sites where this is 1. | `web: /sites/<id>/toggle`, `monitor/retirement.py: sweep` |
| `added_at` | TEXT NOT NULL DEFAULT (NOW()::TEXT) | | `init_db` default |
| `last_success_at` | TIMESTAMPTZ (migration) | Timestamp of the most recent successful USGS fetch. | `db/models.py: record_site_fetch_success` |
| `last_error` | TEXT NOT NULL DEFAULT '' (migration) | Most recent fetch-failure message; cleared on the next success. | `db/models.py: record_site_fetch_error`, `record_site_fetch_success` |
| `last_error_at` | TIMESTAMPTZ (migration) | Timestamp of `last_error`. | `db/models.py: record_site_fetch_error` |
| `origin` | TEXT NOT NULL DEFAULT 'admin' CHECK (`admin`, `user`) (migration) | `admin` = added on the portal Sites page; `user` = provisioned by the pin-onboarding flow, eligible for the retirement sweep. | `db/models.py: save_pin` |

## `settings`

| column | type | meaning | written by |
|---|---|---|---|
| `key` | TEXT PRIMARY KEY | Setting name; see [`settings.md`](settings.md) for every key. | `init_db` (seed) |
| `value` | TEXT NOT NULL DEFAULT '' | Setting value, always stored as text. | `init_db` (seed), `db/models.py: set_setting`, `web: /settings/<slug>` |

## `site_conditions`

| column | type | meaning | written by |
|---|---|---|---|
| `id` | SERIAL PRIMARY KEY | | `monitor/polling.py: record_condition` |
| `site_id` | INTEGER NOT NULL REFERENCES sites(id) | | same |
| `checked_at` | TEXT NOT NULL DEFAULT (NOW()::TEXT) | | same |
| `current_value` | DOUBLE PRECISION | Latest USGS reading. | same |
| `unit` | TEXT NOT NULL DEFAULT 'cfs' | `cfs` for discharge, `ft` for gauge height. | same |
| `percentile` | DOUBLE PRECISION | Current value's rank against the site's daily history since `historical_start_year`. | same |
| `severity` | TEXT NOT NULL DEFAULT 'UNKNOWN' | One of `SEVERE LOW`, `LOW`, `NORMAL`, `HIGH`, `SEVERE HIGH`, `UNKNOWN` — see [`alerts.md`](alerts.md). | same |

## `subscribers`

| column | type | meaning | written by |
|---|---|---|---|
| `id` | SERIAL PRIMARY KEY | | — |
| `display_name` | TEXT NOT NULL DEFAULT '' | | `web: /subscribers/add`, webhooks, `TelegramAdapter` |
| `channel` | TEXT NOT NULL | `telegram`, `sms`, `whatsapp`, or `facebook`; unique together with `channel_id`. | same |
| `channel_id` | TEXT NOT NULL | Channel-specific identifier (chat id, phone number, PSID). | same |
| `opted_in_at` | TEXT NOT NULL DEFAULT (NOW()::TEXT) | | `init_db` default |
| `active` | INTEGER NOT NULL DEFAULT 1 | Global broadcast opt-in flag, independent of any `page_subscribers` row. | `web: /subscribers/add`, `web: /subscribers/<id>/remove`, `web: /webhook/twilio` (`JOIN`/`STOP`/`UNSUBSCRIBE`), `web: /webhook/facebook` (`JOIN`), `TelegramAdapter` (`subscribe_chat_globally`, `unsubscribe_chat_everywhere`) |

## `notifications`

| column | type | meaning | written by |
|---|---|---|---|
| `id` | SERIAL PRIMARY KEY | | `monitor/dispatcher.py: log_notification` |
| `subscriber_id` | INTEGER REFERENCES subscribers(id) | Set only for broadcast sends; NULL for per-page site/gauge alerts. | same |
| `site_id` | INTEGER REFERENCES sites(id) | NULL for broadcast and `noaa_transition` items. | same |
| `sent_at` | TEXT NOT NULL DEFAULT (NOW()::TEXT) | | same |
| `channel` | TEXT NOT NULL | | same |
| `message_text` | TEXT NOT NULL | | same |
| `trigger_type` | TEXT NOT NULL | `transition`, `trend`, `reminder`, `noaa_transition`, or `manual` (broadcast). | same |
| `success` | INTEGER NOT NULL DEFAULT 1 | 0/1; whether the adapter send succeeded. | same |
| `error_msg` | TEXT NOT NULL DEFAULT '' | Exception text when `success` is 0. | same |

## `pending_registrations`

| column | type | meaning | written by |
|---|---|---|---|
| `id` | SERIAL PRIMARY KEY | | `monitor/adapters/telegram.py: _record_pending_registration` |
| `channel` | TEXT NOT NULL | Unique together with `channel_id`. | same |
| `channel_id` | TEXT NOT NULL | | same |
| `started_at` | TEXT NOT NULL DEFAULT (NOW()::TEXT) | | `init_db` default |

Row is recorded on every `/start` so the portal can see who has met the bot, and deleted once the chat globally subscribes (`subscribe_chat_globally`).

## `user_pages`

| column | type | meaning | written by |
|---|---|---|---|
| `id` | SERIAL PRIMARY KEY | | `db/models.py: create_user_page`, `create_pin_page` |
| `public_token` | TEXT NOT NULL UNIQUE | Unguessable token for the public `/view/<public_token>` link. | same |
| `edit_token` | TEXT NOT NULL UNIQUE | Unguessable token for the `/edit/<edit_token>` and `/pin/<edit_token>` links. | same |
| `page_name` | TEXT NOT NULL DEFAULT '' | | same |
| `created_at` | TEXT NOT NULL DEFAULT (NOW()::TEXT) | | `init_db` default |
| `active` | INTEGER NOT NULL DEFAULT 1 | Admin visibility flag, independent of the `status` lifecycle. | `web: /admin/pages/<id>/toggle` |
| `owner_chat_id` | BIGINT (migration) | Telegram chat that owns this pin page; NULL for admin-created pages. | `db/models.py: create_pin_page`, `bind_page_to_chat` |
| `pin_lat` | DOUBLE PRECISION (migration) | | `db/models.py: save_pin` |
| `pin_lon` | DOUBLE PRECISION (migration) | | `db/models.py: save_pin` |
| `river_name` | TEXT (migration) | | `db/models.py: save_pin` |
| `sensitivity` | TEXT NOT NULL DEFAULT 'all' CHECK (`floods`, `unusual`, `all`) (migration) | How much this page hears about — see [`alerts.md`](alerts.md). Defaults to `all` so pre-existing admin pages keep every alert; `create_pin_page` overrides this to `unusual` for new pin pages. | `db/models.py: save_pin`, `set_page_sensitivity` |
| `status` | TEXT NOT NULL DEFAULT 'active' CHECK (`pending`, `active`, `paused`, `stopped`) (migration) | `pending` — created but no pin dropped or chat bound yet; `active` — live; `paused` — alerts suppressed (`/pause`); `stopped` — closed (`/stop`), sources released to the retirement sweep. | `db/models.py: create_pin_page`, `bind_page_to_chat`, `save_pin`, `set_page_status` |

## `noaa_gauges`

| column | type | meaning | written by |
|---|---|---|---|
| `id` | SERIAL PRIMARY KEY | | `db/models.py: get_or_create_noaa_gauge`, `save_pin` |
| `lid` | TEXT NOT NULL UNIQUE | NOAA NWPS gauge identifier. | same |
| `station_name` | TEXT NOT NULL DEFAULT '' | | same |
| `current_stage` | DOUBLE PRECISION | | `NoaaPollingThread` (`update_noaa_gauge_condition`) |
| `action_stage` | DOUBLE PRECISION | | `db/models.py: get_or_create_noaa_gauge`, `save_pin` |
| `minor_flood_stage` | DOUBLE PRECISION | | same |
| `moderate_flood_stage` | DOUBLE PRECISION | | same |
| `major_flood_stage` | DOUBLE PRECISION | | same |
| `severity` | TEXT NOT NULL DEFAULT 'Normal' CHECK (`Unknown`, `Normal`, `Action`, `Minor`, `Moderate`, `Major`) | Current NOAA flood category — see [`alerts.md`](alerts.md). | `NoaaPollingThread` (`update_noaa_gauge_condition`) |
| `last_polled_at` | TEXT | | `NoaaPollingThread` (`update_noaa_gauge_condition`) |
| `quality_grade` | TEXT NOT NULL DEFAULT '' (migration) | Flood-prediction letter grade — see [`../explanation/gauge-quality-grading.md`](../explanation/gauge-quality-grading.md). | `ForecastPollingThread` (`set_gauge_quality`) |
| `quality_detail` | TEXT NOT NULL DEFAULT '' (migration) | Plain-English explanation of `quality_grade`. | `ForecastPollingThread` (`set_gauge_quality`) |
| `quality_checked_at` | TIMESTAMPTZ (migration) | | `ForecastPollingThread` (`set_gauge_quality`) |
| `has_forecast` | BOOLEAN (migration) | Deliberate tri-state: `TRUE` = NOAA publishes a forecast, `FALSE` = NOAA positively confirmed it does not, `NULL` = never successfully checked. `NULL` must never collapse into `FALSE`, or a failed fetch would wrongly claim the gauge has no forecast. | `ForecastPollingThread` (`set_gauge_forecast_availability`) |
| `forecast_checked_at` | TIMESTAMPTZ (migration) | | `ForecastPollingThread` (`set_gauge_forecast_availability`) |
| `origin` | TEXT NOT NULL DEFAULT 'admin' CHECK (`admin`, `user`) (migration) | `admin` = added on a landing page editor by an operator; `user` = provisioned by the pin-onboarding flow, eligible for the retirement sweep. | `db/models.py: save_pin` |
| `active` | INTEGER NOT NULL DEFAULT 1 (migration) | 0/1 flag; `NoaaPollingThread` and `ForecastPollingThread` only act on gauges where this is 1. | `monitor/retirement.py: sweep` |

## `page_noaa_gauges`

| column | type | meaning | written by |
|---|---|---|---|
| `page_id` | INTEGER NOT NULL REFERENCES user_pages(id) | Composite primary key with `noaa_gauge_id`. | `db/models.py: link_page_gauge`, `save_pin` |
| `noaa_gauge_id` | INTEGER NOT NULL REFERENCES noaa_gauges(id) | | `db/models.py: link_page_gauge`, `unlink_page_gauge`, `save_pin` |

## `page_subscribers`

| column | type | meaning | written by |
|---|---|---|---|
| `id` | SERIAL PRIMARY KEY | | `db/models.py: add_page_subscriber`, `bind_page_to_chat` |
| `page_id` | INTEGER NOT NULL REFERENCES user_pages(id) | Unique together with `channel` and `channel_id`. | same |
| `channel` | TEXT NOT NULL | | same |
| `channel_id` | TEXT NOT NULL | | same |
| `display_name` | TEXT NOT NULL DEFAULT '' | | same |
| `status` | TEXT NOT NULL DEFAULT 'active' CHECK (`active`, `paused`, `unsubscribed`) | Gates whether this subscriber receives this page's alerts. | `db/models.py: add_page_subscriber`, `set_page_subscriber_status`, `bind_page_to_chat`, `web: /webhook/twilio` (`PAUSE`/`RESUME`/`STOP`/`UNSUBSCRIBE`) |
| `opted_in_at` | TEXT NOT NULL DEFAULT (NOW()::TEXT) | | `init_db` default |

## `page_sites`

| column | type | meaning | written by |
|---|---|---|---|
| `page_id` | INTEGER NOT NULL REFERENCES user_pages(id) | Composite primary key with `site_id`. Site alerts route only to pages that link the site here. | `db/models.py: link_page_site`, `save_pin` |
| `site_id` | INTEGER NOT NULL REFERENCES sites(id) | | `db/models.py: link_page_site`, `unlink_page_site`, `save_pin` |

## `noaa_observations`

| column | type | meaning | written by |
|---|---|---|---|
| `id` | SERIAL PRIMARY KEY | | `db/models.py: record_noaa_observation` |
| `lid` | TEXT NOT NULL | Unique together with `observed_at`. | same |
| `observed_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | | same |
| `stage` | DOUBLE PRECISION NOT NULL | Archived on every `NoaaPollingThread` pass, transition or not, so forecasts can later be graded against what actually happened. | `NoaaPollingThread` |

## `gauge_forecasts`

| column | type | meaning | written by |
|---|---|---|---|
| `id` | SERIAL PRIMARY KEY | | `db/models.py: record_forecast_points` |
| `lid` | TEXT NOT NULL | Unique together with `issued_at` and `valid_at`. | same |
| `issued_at` | TIMESTAMPTZ NOT NULL | When NOAA issued this forecast. | same |
| `valid_at` | TIMESTAMPTZ NOT NULL | The moment this forecast point predicts. | same |
| `predicted_stage` | DOUBLE PRECISION NOT NULL | | `ForecastPollingThread` |

## Migration policy

`MIGRATION_STATEMENTS` in `db/models.py` runs after `SCHEMA_STATEMENTS` on every `init_db` call, and is additive only: every statement is `ADD COLUMN IF NOT EXISTS` or `CREATE INDEX IF NOT EXISTS`, safe to re-run, and never drops or rewrites an existing column. A column that needs to change gets a new column and a read-time fallback, not an `ALTER COLUMN`.

## Indexes

- `idx_site_conditions_site_id` on `site_conditions (site_id, id DESC)`
- `idx_notifications_site_trigger` on `notifications (site_id, trigger_type, id DESC)`
- `idx_user_pages_owner_chat` on `user_pages (owner_chat_id)`

Verified against commit c12d91c
