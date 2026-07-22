# Settings Sub-Pages by Type

**Date:** 2026-07-21
**Status:** Approved design — ready for implementation planning

## Problem

The Settings page renders all 16 runtime settings in a single flat form with
one Save button (`SETTINGS_FIELDS` → `settings.html`). It's a long,
undifferentiated list mixing unrelated concerns (polling cadence, alert
thresholds, and per-channel credentials). Group them into sub-pages by type.

## Decisions (from brainstorming)

- **Three groups:** Monitoring · Alert Thresholds · Notification Channels
  (all channels on one page, visually sub-grouped by provider).
- **Separate URLs, save-per-page:** each group is its own route; its Save
  button persists only that group's fields — no cross-group overwrites.
- Keep the pre-existing password-prefill behavior (out of scope to change).

## Data model

Replace the flat `SETTINGS_FIELDS` list in `web/routes.py` with an ordered
`SETTINGS_GROUPS` structure. Each group: `slug`, `title`, and `sections`; a
section is `{subtitle, fields}` where `fields` are the existing
`(key, label, input_type)` tuples and `subtitle` is `None` (rendered flat) or
a heading string.

- `monitoring` — "Monitoring": poll_interval_minutes, historical_start_year,
  search_radius_miles (one unsubtitled section).
- `thresholds` — "Alert Thresholds": low/high/very_low/very_high percentiles,
  reminder_low_high_hours, reminder_severe_hours (one unsubtitled section).
- `channels` — "Notification Channels": three subtitled sections —
  "Telegram" (telegram_bot_token); "Twilio (SMS / WhatsApp)"
  (twilio_account_sid, twilio_auth_token, twilio_sms_number,
  twilio_whatsapp_number); "Facebook Messenger" (facebook_page_token,
  facebook_verify_token).

A flattened `SETTINGS_FIELDS = [f for g in SETTINGS_GROUPS for s in
g["sections"] for f in s["fields"]]` is kept so the module still exposes the
old name (harmless; nothing else imports it, but it avoids surprises).

## Routes (`web/routes.py`)

- `GET /settings` — keeps the `settings` endpoint (the `base.html` nav link
  points at `/settings`); redirects to the first group,
  `/settings/monitoring`.
- `GET|POST /settings/<slug>` (`settings_group`): resolve the group by slug
  (unknown → `abort(404)`). GET renders that group's sections plus the tab
  nav. POST persists only that group's keys (`request.form.get(key, "")` for
  each), flashes "Settings saved.", and redirects back to the same slug.

## Template (`web/templates/settings.html`)

A Bootstrap `nav-tabs` across the three groups (active tab = current group,
each linking to `/settings/<slug>` via `url_for('settings_group', ...)`), then
the active group's form posting to `/settings/<active.slug>`: for each section,
render its subtitle heading when present, then its fields (same row/label/input
markup as today, preserving `autocomplete="new-password"` on password inputs).
One "Save <group title>" button. The route passes `groups`, `active`, and
`current` (only the active group's keys).

## Testing

- `GET /settings` → 302 redirect to `/settings/monitoring`.
- `GET /settings/monitoring` → 200, shows "Poll Interval", and renders all
  three tab titles (Monitoring, Alert Thresholds, Notification Channels).
- `GET /settings/channels` → shows the three provider subtitles.
- `GET /settings/bogus` → 404.
- `POST /settings/monitoring` with the monitoring fields saves
  `poll_interval_minutes`; redirect lands 200.
- Cross-group isolation: set a monitoring value, then `POST /settings/channels`
  (channel fields only) → the monitoring value is unchanged.
- Update `test_app.py::test_settings_page_returns_200` to follow the redirect
  (or assert 302).

## Out of scope (YAGNI)

- No change to which settings exist, their keys, or storage (`set_setting`/
  `get_setting` unchanged).
- No change to password prefill behavior.
- No new settings, no reordering of fields within a group beyond the grouping.
