# HTTP routes

Every HTTP route, grouped by how it is protected.

## Portal (admin)

Guarded by HTTP Basic auth (`web/auth.py`); endpoint names here are not in `PUBLIC_ENDPOINTS`.

| method | path | auth | purpose | rate limit |
|---|---|---|---|---|
| GET | `/` | admin | Render the dashboard with active sites' latest conditions and the 20 most recent notifications. | — |
| GET | `/admin/pages` | admin | List all landing pages with gauge and active-subscriber counts. | — |
| POST | `/admin/pages/<int:page_id>/toggle` | admin | Flip a page's active flag and redirect to the admin list. | — |
| GET, POST | `/broadcast` | admin | View the broadcast form, or queue a broadcast message to selected channels. | — |
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
| POST | `/edit/<edit_token>/subscribe` | token | Subscribe a recipient to the page's alerts. | — |
| POST | `/edit/<edit_token>/unsubscribe` | token | Change a page subscriber's status. | — |
| GET | `/view/<public_token>` | token | Render a public landing page, or 404 if missing/inactive. | — |

## Pin onboarding (token)

Also guarded by `edit_token` in the URL; additionally throttled because each call can trigger third-party API lookups.

| method | path | auth | purpose | rate limit |
|---|---|---|---|---|
| GET | `/pin/<edit_token>` | token | The map screen. | — |
| POST | `/pin/<edit_token>/discover` | token | Propose USGS/NOAA gauges for a lat/lon. | 20 per token and 60 per IP per hour |
| POST | `/pin/<edit_token>/save` | token | Store the pin and provision its sources. | 10 per token per hour, max 30 sources |

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
| GET, POST | `/pages/new` | open | View the page-creation form (GET), or create a public landing page (POST). | — |
| GET | `/pin` | open | Web-first entry: create a pending page and open its map. | 10 per IP per day |

Verified against commit c12d91c
