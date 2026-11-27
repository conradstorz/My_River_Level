# HTTP routes

Every HTTP route, grouped by how it is protected.

## Portal (admin)

Guarded by HTTP Basic auth (`web/auth.py`); endpoint names here are not in `PUBLIC_ENDPOINTS`.

| method | path | auth | purpose | rate limit |
|---|---|---|---|---|
| GET | `/` | admin | Render the dashboard with active sites' latest conditions and the 20 most recent notifications. | — |
| GET | `/admin/pages` | admin | List all landing pages with gauge and active-subscriber counts. | — |
| POST | `/admin/pages/<int:page_id>/toggle` | admin | Flip a page's active flag and redirect to the admin list. | — |
| POST | `/edit/<edit_token>/subscribe` | admin | Subscribe a recipient to a page's alerts; the page editor shows this form only to the maintainer. | — |
| GET, POST | `/broadcast` | admin | View the broadcast form, or queue a broadcast message to selected channels. | — |
| GET, POST | `/pages/new` | admin | View the page-creation form (GET), or create a public landing page (POST). | — |
| GET, POST | `/settings` | admin | Redirect to the first settings sub-page. | — |
| GET, POST | `/settings/<slug>` | admin | View or save one group of settings. | — |
| GET | `/sites` | admin | List every monitored site, with health, plus the add/search forms. | — |
| POST | `/sites/<int:site_id>/remove` | admin | Delete a site and redirect back. | — |
| POST | `/sites/<int:site_id>/toggle` | admin | Flip a site's active flag and redirect back. | — |
| POST | `/sites/add` | admin | Validate a USGS site number and add it. | — |
| GET | `/sites/search` | admin | Paginated USGS+NOAA gauge search (`?q=&page=`). | — |
| GET | `/subscribers` | admin | List all subscribers newest first. | — |
| POST | `/subscribers/<int:sub_id>/remove` | admin | Deactivate a subscriber and redirect back. | — |
| POST | `/subscribers/add` | admin | Upsert a subscriber from the form. | — |

## Landing pages (token)

Guarded by an unguessable `public_token` or `edit_token` in the URL, not by Basic auth; endpoint names are in `PUBLIC_ENDPOINTS`.

| method | path | auth | purpose | rate limit |
|---|---|---|---|---|
| GET | `/edit/<edit_token>` | token | Render the page editor with its gauges and active subscribers, or 404. | — |
| POST | `/edit/<edit_token>/gauges/add` | token | Add a NOAA gauge to the page. | — |
| POST | `/edit/<edit_token>/gauges/remove` | token | Unlink a gauge from the page and redirect to the editor (404 on bad token). | — |
| GET | `/edit/<edit_token>/gauges/search` | token | Find NOAA gauges by name (`?q=&page=`). | — |
| POST | `/edit/<edit_token>/sensitivity` | token | Set how much this page hears about. | — |
| POST | `/edit/<edit_token>/sites/add` | token | Attach a USGS river gauge to the page. | — |
| POST | `/edit/<edit_token>/sites/remove` | token | Detach a USGS gauge from the page. | — |
| POST | `/edit/<edit_token>/unsubscribe` | token | Change a page subscriber's status (`active`, `paused` or `unsubscribed`; anything else 400). | — |
| GET | `/view/<public_token>` | token | Render a public landing page, or 404 if missing/inactive. | — |

## Pin onboarding (token)

Also guarded by `edit_token` in the URL; additionally throttled because each call can trigger third-party API lookups. Only Pin pages are served — a page bound to a Telegram chat, or one still `pending`; an admin-created page's token returns 404 on every route here. "Per IP" means per visitor key: an IPv4 address, or an IPv6 address's /64.

| method | path | auth | purpose | rate limit |
|---|---|---|---|---|
| GET | `/pin/<edit_token>` | token | The map screen. | — |
| POST | `/pin/<edit_token>/discover` | token | Propose USGS/NOAA gauges for a lat/lon (NOAA pairing looked up for the nearest 10 USGS sites). | 20 per token and 60 per IP per hour; at most 2 in flight per IP (429 beyond) |
| POST | `/pin/<edit_token>/save` | token | Store the pin and provision its sources. | 10 per token and 30 per IP per hour; max 20 sources (400 beyond) |

## Webhooks (signature)

Guarded by the provider's own request signature (`verify_twilio_signature`, `verify_facebook_signature`), not by Basic auth or a token; fail closed when the corresponding secret setting is unset.

| method | path | auth | purpose | rate limit |
|---|---|---|---|---|
| GET, POST | `/webhook/facebook` | signature | Verify the webhook (GET) and handle inbound Messenger messages (POST). | — |
| POST | `/webhook/twilio` | signature | Handle inbound Twilio SMS/WhatsApp keywords (`JOIN`, `STOP`/`UNSUBSCRIBE`, `PAUSE`/`RESUME`). | — |
| POST | `/webhook/twilio/status` | signature | Twilio delivery status callback (delivered/undelivered/failed). | — |

## Open

Neither Basic auth, a URL token, nor a provider signature.

| method | path | auth | purpose | rate limit |
|---|---|---|---|---|
| GET | `/healthz` | open | 200 when every registered thread is alive, else 503. | — |
| GET | `/pin` | open | Web-first entry: create a pending page and open its map. | 10 per IP (IPv6: per /64) per day |

## Applies to every route

- A request body over 64 KB (`MAX_REQUEST_BYTES` in `web/app.py`) is refused with 413, before auth or routing.
- A page name (`POST /pages/new`) or river name (`POST /pin/<edit_token>/save`) must be text of at most 100 characters after trimming; anything else is a 400 with a message.
- Every response, errors included, carries `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'`.

Verified against commit 20c729b
