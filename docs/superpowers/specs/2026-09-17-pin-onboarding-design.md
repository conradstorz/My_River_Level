# Pin-on-a-map onboarding — design

Date: 2026-09-17
Status: approved for planning

## Goal

A member of the public should be able to drop a pin on a map anywhere in the
United States, have the system work out which gauges represent that stretch of
waterway, confirm the list, and then receive Telegram messages whenever the
waterway does something out of the ordinary. No gauge IDs, no admin
involvement, no account.

## Decisions taken during brainstorming

| Question | Decision |
|---|---|
| Biggest gap | Onboarding: pick a spot on a map, data acquisition is automatic |
| Which gauges are "theirs" | Same main stem via USGS NLDI, user confirms a proposed list, can remove sources later |
| Identity | Channel-first: the Telegram chat owns the page. Accounts deferred |
| Channels for v1 | Telegram only. Other channels are separate specs |
| Tuning | One sensitivity dial: `floods` / `unusual` / `all` |
| Discovery method | NLDI network navigation, bounding-box search as fallback |

## Out of scope

Additional channels (Signal, WhatsApp, Discord, email), user accounts,
multiple pins per chat, changes to alert wording, changes to the polling
algorithms.

## 1. User flow

Two entry points converge on one page record.

**Telegram-first.** The user sends `/start` with no argument. The bot creates a
`user_pages` row with `owner_chat_id` set, `status = 'pending'`, and replies
with the map link `/pin/<edit_token>`.

**Web-first.** The user opens `/pin`. The server creates a pending page with
no owner and redirects to `/pin/<edit_token>`. After placing the pin the user
clicks "Send alerts to Telegram", which opens
`https://t.me/<bot_username>?start=<edit_token>`. The bot receives
`/start <edit_token>`, sets `owner_chat_id` on that page, and confirms. The
bot username is read from the Telegram API at bot start (`getMe`) and cached
in the settings table as `telegram_bot_username` so the web page can build
the link.

**Map screen** (`/pin/<edit_token>`):

- Leaflet map with OpenStreetMap tiles, centred on the continental US, or on
  the browser's geolocation if granted.
- Click places or moves a single marker. A "Find gauges" button posts the
  coordinates to `/pin/<edit_token>/discover`.
- Result panel: river name (or "No waterway found here; showing nearest
  gauges"), then a checkbox list of candidates, all checked by default. Each
  row shows type (USGS / NOAA), name, id, distance from the pin, and a tag:
  `upstream`, `downstream`, `nearby`, or `nearest`.
- Sensitivity radio: *Floods only* / *Floods and unusual levels* (default) /
  *Everything, including rapid changes*.
- "Save" posts to `/pin/<edit_token>/save`. The page becomes `active` if it
  has an owner, otherwise stays `pending` until the Telegram deep link binds
  it. Saving with zero sources is rejected with a visible message.
- After save, the bot (if bound) sends a confirmation listing the sources and
  the `/settings` hint.

**Chat commands** (in addition to the existing `/subscribe`, `/unsubscribe`,
`/mypages`):

| Command | Effect |
|---|---|
| `/start` | Create pending page and send map link (or bind with a token) |
| `/settings` | Reply with the edit link for the chat's page |
| `/sensitivity` | Inline keyboard with the three levels; tapping one saves it |
| `/sources` | List sources with a "Remove" inline button per row |
| `/pause` / `/resume` | Toggle `status` between `paused` and `active` |
| `/stop` | Set `status = 'stopped'`; sources are released for retirement |

A chat owns at most one page in v1. `/start` on a chat that already owns a
page replies with the existing edit link instead of creating another.

**Returning on the web.** The existing `/edit/<edit_token>` page gains the
sensitivity control and per-source remove buttons. The edit token is only ever
delivered through the chat, so possession of the chat is possession of the
page.

## 2. Gauge discovery — `monitor/pin_discovery.py`

(`monitor/gauge_discovery.py` already exists and does name-based search; the
pin flow gets its own module.)

Public function:

```python
def discover(lat: float, lon: float, *, reach_km: float, fallback_radius_miles: float) -> Discovery
```

`Discovery` is a dataclass: `river_name: str | None`, `snap: Literal["on_network", "off_network", "failed"]`, `candidates: list[Candidate]`.
`Candidate`: `kind: Literal["usgs", "noaa"]`, `id: str`, `name: str`, `distance_km: float`, `tag: Literal["upstream", "downstream", "nearby", "nearest"]`, `usgs_id: str | None` (for NOAA gauges that publish one).

The function touches no database. It uses `requests` with a 15 s timeout and
a shared session.

Steps:

1. **Snap.** `GET https://api.water.usgs.gov/nldi/linked-data/comid/position?coords=POINT(<lon> <lat>)`.
   Returns the nearest flowline feature with its COMID and GNIS name. If the
   pin is more than `SNAP_MAX_KM = 2.0` from the returned flowline (computed
   from the feature geometry), `snap = "off_network"` and steps 2–4 are
   skipped.
2. **Navigate.** `GET .../linked-data/comid/<comid>/navigation/UM/nwissite?distance=<reach_km>`
   and the same for `DM`. Each returns USGS sites on the main stem with their
   `identifier` (`USGS-01234567`) and geometry. Tag `upstream` / `downstream`.
   Distance is great-circle distance from the pin.
3. **Parameter check.** `nwis.get_info(sites=[...])` for the collected site
   numbers; keep only sites whose available parameters include `00060`
   (discharge) or `00065` (gage height). Sites returning no info are dropped.
4. **NOAA gauges.** NLDI has no NWPS layer. `monitor/noaa_client.py` gains
   `gauges_near(lat, lon, radius_miles)` using the NWPS `GET /gauges` list
   endpoint with a bounding box, returning `lid`, `name`, `usgsId`, and
   coordinates. Gauges whose `usgsId` matches a candidate USGS site inherit
   its tag; others within the radius are tagged `nearby`.
5. **Fallback.** If steps 1–2 yield no USGS candidates (off-network, empty
   navigation, or any exception), run the existing bounding-box search
   (`nwis.what_sites` around the pin using `fallback_radius_miles`) and tag
   the results `nearest`, then still run step 4. Any step that raises logs
   the step name and the exception at WARNING and the function continues.

The result is sorted upstream-first by distance, then downstream, then
nearby/nearest.

Settings added: `discovery_reach_km` (default 50). The fallback radius reuses
the existing `search_radius_miles`.

## 3. Data model and automatic acquisition

All changes are additive migrations in `db/models.py`, applied by the existing
`init_db` path with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

**`user_pages`** gains:

| Column | Type | Notes |
|---|---|---|
| `owner_chat_id` | BIGINT NULL | Telegram chat id; NULL = admin-created or unbound web-first page |
| `pin_lat`, `pin_lon` | DOUBLE PRECISION NULL | |
| `river_name` | TEXT NULL | From NLDI GNIS name |
| `sensitivity` | TEXT NOT NULL DEFAULT 'unusual' | CHECK IN ('floods','unusual','all') |
| `status` | TEXT NOT NULL DEFAULT 'active' | CHECK IN ('pending','active','paused','stopped') |

The existing `active` flag stays as the admin kill switch; alert routing
requires both `active = 1` and `status = 'active'`. Pending pages with no
`pin_lat` older than 24 hours are deleted by the retirement sweep.

**`sites`** and **`noaa_gauges`** gain `origin TEXT NOT NULL DEFAULT 'admin'`
(CHECK IN ('admin','user')). `noaa_gauges` has no active flag today; it gains
`active INTEGER NOT NULL DEFAULT 1`, and `noaa_polling.py` and
`forecast_polling.py` skip rows with `active = 0`.

**Provisioning on save.** In one transaction: each chosen USGS site not
present in `sites` is validated with `site_validation` and inserted with
`origin = 'user'`, `active = 1`; each chosen NOAA gauge not present in
`noaa_gauges` is inserted with `origin = 'user'`; `page_sites` and
`page_noaa_gauges` rows are written; the page's pin, river name and
sensitivity are stored. A site that fails validation is skipped and reported
in the save response. The pollers need no change: they already poll every
active site and gauge, and the daily-values backfill already runs on a site's
first poll.

**Retirement sweep** (new function called from the scheduler thread once per
hour): deactivate (`active = 0`) any `sites` or `noaa_gauges` row with
`origin = 'user'` that has no reference from a live page (`status` `active`
or `paused`, or `pending` with a pin saved). Rows are never deleted; history
and conditions are kept. Re-selecting a retired site reactivates it.
Admin-origin rows are never touched. The sweep also deletes stale pending
pages as above. A pinned page still `pending` after 7 days (never connected
to Telegram) is deleted too, and its sources retire on the following sweep.

**Sensitivity at routing time.** The pollers keep detecting everything. The
existing per-page routing in `polling.py` / `noaa_polling.py` (subscribers of
active pages referencing the site) gains a filter on the page's sensitivity:

| Level | Admitted alerts |
|---|---|
| `floods` | NOAA category changes; USGS SEVERE_HIGH transitions and their reminders |
| `unusual` | `floods` plus USGS LOW, HIGH, SEVERE_LOW transitions and reminders |
| `all` | `unusual` plus rise/fall rate alerts |

The filter lives in one function, `alert_allowed(sensitivity, alert_kind, severity) -> bool`, in `monitor/scheduler.py`, so both pollers share it.

**De-authorising a source** is deleting the `page_sites` / `page_noaa_gauges`
row (existing helpers). The sweep handles the rest.

## 4. Web, security, admin

**Routes** (public, token is the credential, added to `PUBLIC_ENDPOINTS`):

| Route | Purpose |
|---|---|
| `GET /pin` | Create a pending page, redirect to its map |
| `GET /pin/<edit_token>` | Map page |
| `POST /pin/<edit_token>/discover` | JSON `{lat, lon}` → `Discovery` as JSON |
| `POST /pin/<edit_token>/save` | JSON `{lat, lon, river_name, sensitivity, sources: [{kind, id}]}` |

Leaflet is loaded from a pinned CDN version; tiles from OpenStreetMap with the
required attribution. No API keys.

**Abuse limits** (in-process, per worker, simple token-bucket in
`web/ratelimit.py`): `/discover` at 20 per hour per token and 60 per hour per
IP; `GET /pin` at 10 pending pages per IP per day. Exceeding returns 429 with
a plain message. A save with zero sources returns 400.

**Telegram.** Command handlers move from `monitor/adapters/telegram.py` into
`monitor/adapters/telegram_commands.py`; the bot runner, token hot-reload and
`send` stay where they are. New handlers as listed in section 1. Inline
keyboard callbacks carry `sens:<level>` and `rm:<kind>:<id>` payloads bound
to the chat's page; a callback for a page the chat does not own is ignored.

**Admin portal.** Sites and NOAA gauge lists show an origin badge and the
count of pages referencing each row. `/admin/pages` shows pin coordinates,
river name, owner chat id and status, and can still toggle `active`.

## 5. Testing

- `tests/monitor/test_pin_discovery.py`: recorded NLDI and NWPS JSON
  fixtures for on-network with both directions populated, off-network snap,
  empty navigation with bbox fallback, NLDI HTTP error with fallback, NOAA
  gauge matched by `usgsId`, NOAA gauge tagged nearby, parameter-check
  filtering. All HTTP mocked; no network in tests.
- `tests/web/test_pin.py`: pending page creation, map page renders, discover
  returns candidates, save provisions sites and gauges with `origin='user'`,
  zero-source rejection, rate limits, deep-link binding sets `owner_chat_id`,
  `/start` on an owning chat returns the existing link.
- `tests/monitor/test_sensitivity.py`: `alert_allowed` truth table, plus one
  routing test per poller proving a `floods` page does not receive a HIGH
  transition and an `all` page receives a rate alert.
- `tests/db/test_retirement.py`: user-origin site with no active page is
  deactivated, admin-origin untouched, paused page keeps its sites, stale
  pending page deleted, re-selection reactivates.
- Telegram command tests follow the existing pattern in
  `tests/monitor/test_telegram_adapter.py`.
