# Settings

Every key in the `settings` table, seeded with these defaults by `init_db` and editable on the portal Settings page; the thread named in *Read by* re-reads the value on its next cycle, so changes take effect without a restart.

## Monitoring

| key | type | default | unit | effect | read by |
|---|---|---|---|---|---|
| `discovery_reach_km` | number | `50` | km | How far along the river the pin-discovery search looks for USGS/NOAA gauges near a dropped pin. | `web: /pin/<edit_token>/discover` |
| `historical_start_year` | number | `1980` | year | Daily USGS history is fetched from this year forward to build the distribution percentiles are ranked against. | `PollingThread` |
| `poll_interval_minutes` | number | `15` | minutes | How often USGS and NOAA gauges are re-fetched. | `PollingThread`, `NoaaPollingThread` |
| `public_base_url` | text | `` (empty) | — | Base URL used to build the map, editor, and Telegram deep links; when unset, links are omitted and the bot sends `NO_BASE_URL` text instead. | `TelegramAdapter` |
| `search_radius_miles` | number | `25` | miles | Fallback discovery radius when the river-reach search around a pin finds nothing. | `web: /pin/<edit_token>/discover` |

## Alert Thresholds

| key | type | default | unit | effect | read by |
|---|---|---|---|---|---|
| `high_percentile` | number | `90` | percentile | A reading at or above this percentile (and below `very_high_percentile`) of the site's daily history since `historical_start_year` is HIGH. | `PollingThread` |
| `low_percentile` | number | `10` | percentile | A reading at or below this percentile (and above `very_low_percentile`) is LOW. | `PollingThread` |
| `reminder_low_high_hours` | number | `24` | hours | How often a LOW or HIGH condition repeats its reminder alert while it persists. | `SchedulerThread` |
| `reminder_severe_hours` | number | `4` | hours | How often a SEVERE LOW or SEVERE HIGH condition repeats its reminder alert while it persists. | `SchedulerThread` |
| `very_high_percentile` | number | `95` | percentile | A reading at or above this percentile of the site's daily history since `historical_start_year` is SEVERE HIGH. | `PollingThread` |
| `very_low_percentile` | number | `5` | percentile | A reading at or below this percentile of the site's daily history since `historical_start_year` is SEVERE LOW. | `PollingThread` |

## Notification Channels

Credentials are stored as plain settings values, not environment variables; the field type below is `secret` where the portal renders a password input.

| key | type | default | unit | effect | read by |
|---|---|---|---|---|---|
| `facebook_app_secret` | secret | `` (empty) | — | Signs the `X-Hub-Signature-256` check on inbound Facebook webhooks; unset, every Facebook webhook request is rejected. | `web: /webhook/facebook` |
| `facebook_page_token` | secret | `` (empty) | — | Facebook Page access token used to send outbound Messenger replies. | `FacebookAdapter` |
| `facebook_verify_token` | secret | `` (empty) | — | Shared secret Facebook must echo back as `hub.verify_token` during webhook subscription verification. | `web: /webhook/facebook` |
| `telegram_bot_token` | secret | `` (empty) | — | Telegram Bot API token; the bot supervisor waits for this to be set, and restarts the bot within `TelegramAdapter.TOKEN_POLL_SECONDS` (30 s) of it changing. | `TelegramAdapter` |
| `telegram_bot_username` | text | `` (empty) | — | The bot's own `@username`, written automatically once the bot connects; used to build the `/start` deep link (`https://t.me/<bot-username>?start=<edit_token>`). Written by `TelegramAdapter` at bot start. | `web: /pin/*` |
| `twilio_account_sid` | secret | `` (empty) | — | Twilio Account SID used to authenticate outbound SMS/WhatsApp sends. | `SMSAdapter`, `WhatsAppAdapter` |
| `twilio_auth_token` | secret | `` (empty) | — | Twilio Auth Token; also verifies `X-Twilio-Signature` on inbound Twilio webhooks. | `SMSAdapter`, `WhatsAppAdapter`, `web: /webhook/twilio` |
| `twilio_sms_number` | secret | `` (empty) | — | The Twilio phone number outbound SMS is sent from. | `SMSAdapter` |
| `twilio_whatsapp_number` | secret | `` (empty) | — | The Twilio WhatsApp-enabled number outbound WhatsApp messages are sent from; also used to tell an inbound WhatsApp message from SMS by the webhook's `To` number. | `WhatsAppAdapter`, `web: /webhook/twilio` |

## Rate of change

Not grouped on the portal Settings page — `SETTINGS_GROUPS` in `web/routes.py` has no form fields for these four keys, so they are database-only: edit them with the one-liner in [`cli.md`](cli.md) or directly with `psql`.

| key | type | default | unit | effect | read by |
|---|---|---|---|---|---|
| `rate_change_min_interval_hours` | number | `6` | hours | Minimum time between two trend alerts for the same site, so a sustained rise or fall doesn't re-alert every poll. | `PollingThread` |
| `rate_change_threshold_ft` | number | `2.0` | ft | Minimum absolute change in gauge height (parameter `00065`) within the trend window needed to raise a trend alert. | `PollingThread` |
| `rate_change_threshold_pct` | number | `25` | percent | Minimum percentage change from the trend window's starting value, for parameters other than gauge height, needed to raise a trend alert. | `PollingThread` |
| `rate_change_window_hours` | number | `6` | hours | How far back a site's rate of change is measured over. | `PollingThread` |

## Data health

Also database-only, with no portal Settings form field.

| key | type | default | unit | effect | read by |
|---|---|---|---|---|---|
| `forecast_poll_hours` | number | `6` | hours | How often `ForecastPollingThread` archives each NOAA gauge's published forecast and re-grades flood-prediction accuracy. | `ForecastPollingThread` |
| `site_stale_hours` | number | `6` | hours | A site with no successful USGS fetch within this many hours is flagged stale on the Sites page. | `web: /sites` |

Verified against commit c12d91c
