# Security model

River Monitor has no single login for everyone — different routes are
guarded by different mechanisms depending on who is supposed to be able to
reach them, and each one is designed to fail closed: a missing or malformed
piece of configuration denies a request rather than quietly letting it
through.

`web/auth.py` guards every portal route whose endpoint name is not listed in
`PUBLIC_ENDPOINTS` with HTTP Basic auth. Credentials come from the
environment (`ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH` or `ADMIN_PASSWORD`),
never the database — the database is exactly what the portal is protecting,
so the keys to it cannot also live inside it. With neither password variable
set, or with a hash that does not parse into `method$salt$hash`, every guarded
route returns 503 rather than 401: a 401 would send an operator hunting for a
typo in a password that was never the problem, and — more importantly —
neither situation is a wrong password, so treating it as "deny by default,
loudly" is safer than any interpretation that could resolve to "allow."

The landing-page family works differently: `/view/<public_token>`,
`/edit/<edit_token>`, and `/pin/<edit_token>` carry no Basic-auth challenge at
all, because their protection is the token itself — an unguessable UUID
embedded in the URL. Anyone holding a `public_token` can view that page's
gauges and their live conditions, nothing more. Anyone holding an
`edit_token` can add or remove the page's gauges, remove or pause its
existing subscribers, set its sensitivity, and drive the whole pin flow (map, discover, save) for that
one page — full control over that page's own sources and audience, but
no route into any other page or the admin portal. What an edit token cannot
do is add a recipient: `POST /edit/<edit_token>/subscribe` requires Basic
auth, and the editor shows its form only to the maintainer, so a stranger
cannot point a page's alerts at someone else's Telegram chat or phone. Pin
pages get their subscriber only from the Telegram `/start` binding. Losing
an edit link is equivalent to losing write access to that one page.

The two provider webhooks are authenticated by request signature instead of
either mechanism above, in `web/routes.py`: an inbound Twilio request is checked against
`X-Twilio-Signature` using Twilio's own validator over the full request URL
and form body, and an inbound Facebook request is checked against
`X-Hub-Signature-256`, an HMAC-SHA256 of the raw body keyed by the app
secret. Both fail closed the same way the portal does — an unset
`twilio_auth_token` or `facebook_app_secret` rejects every inbound webhook
call rather than accepting unsigned ones, since accepting them would let
anyone impersonate a subscriber's `JOIN`/`STOP` command or a Messenger
message.

The Telegram `/start <edit_token>` deep link binds a page to whichever chat
taps it, and the person who sent the link keeps its edit token. So a chat
that already has a live pin page is never rebound by tapping a different
page's link: the existing page stays live, the new page stays unbound, and
the bot tells the user to send `/stop` first if they really mean to switch.
Only a deliberate `/stop` from the chat itself releases its page (see
[`telegram-commands.md`](../reference/telegram-commands.md)). The deep link
binds only Pin pages — an unowned page still `pending`, or one the chat
already owns. An admin-created page's edit token is refused with a bot reply
and nothing changes, so a leaked admin edit link cannot be turned into a Pin
page whose curated sources the public pin save would then replace.

The pin routes are the one place an anonymous visitor's click triggers real
third-party API calls (NLDI, NWPS, USGS), so `web/ratelimit.py`'s
`RateLimiter` throttles them: 10 new pending pages per IP per day, 20
discovery searches per token and 60 per IP per hour, and 10 saves per token
plus 30 per IP per hour (the per-IP limit stops a visitor bypassing the
per-token one by minting fresh pages). A `ConcurrencyLimiter` lets one IP
have at most 2 discovery searches in flight, so a few slow searches cannot
occupy every waitress thread, and each search pairs at most the nearest 10
USGS sites with NOAA gauges. A save may choose at most
`MAX_SOURCES_PER_PAGE` (20) sources, checked before any of them is verified.
The pin routes serve only Pin pages — bound to a Telegram chat, or still
`pending` — so an admin-created page's token cannot be driven through them.
The page editor (`/edit/<edit_token>`) is bounded the same way: every write
shares a budget of 60 per IP per hour and the NOAA name search allows 30 per
IP per hour (429 beyond, and keyed by visitor, so minting more pages does not
reset them); an add is refused with a flash once the page already has
`MAX_SOURCES_PER_PAGE` sources, before the gauge is looked up upstream; and a
source the editor adds for the first time is `user`-origin, so it retires
once no live page references it.
An IPv6 visitor is keyed by its /64 (`client_key`), since one subscriber
typically holds a whole /64 and could otherwise rotate addresses to reset
every limit. It is explicitly an in-process, per-container brake, not a
distributed quota — good enough to stop one browser hammering an upstream
API, not a guarantee across multiple app replicas. What counts as "an IP" for
these limits depends on `TRUSTED_PROXY_COUNT`: with it at the default of 0,
Flask's `ProxyFix` is not installed at all and every request is attributed to
whatever address made the TCP connection, which behind an unconfigured
reverse proxy is the proxy itself; only a correctly set proxy hop count makes
`X-Forwarded-For` trustworthy, and thus makes the per-IP limits mean anything
at scale (see [`reverse-proxy.md`](../howto/reverse-proxy.md)).

A NOAA or USGS station name is third-party text that ends up rendered back
to visitors — on `web/templates/page_view.html` and `web/templates/page_edit.html` it goes through
ordinary Jinja `{{ }}` interpolation, which HTML-escapes it automatically;
on the pin map, where candidate names arrive over `fetch` as JSON rather
than through a server-rendered template, the page's own script escapes each
field by hand before building markup from it. Neither path ever inserts a
gauge name unescaped.

## Baseline hygiene on every response

`web/app.py` applies a few blanket rules no matter which guard a route uses.
Request bodies over 64 KB are refused with 413 before the auth guard or the
route sees them, since nothing legitimate is anywhere near that size and the
pin routes are open to anonymous visitors. Visitor-supplied names (the page
name on `/pages/new`, the river name on a pin save) must be text of at most
100 characters, so a crafted JSON body gets a 400 rather than a 500 or an
unbounded row. Every response, including error pages, carries
`Referrer-Policy: no-referrer` (edit and view links are bearer tokens in the
URL, so they must not leak to third-party hosts in a `Referer`),
`X-Content-Type-Options: nosniff`, and both `X-Frame-Options: DENY` and
`Content-Security-Policy: frame-ancestors 'none'` against clickjacking. Every
Bootstrap and Leaflet tag loaded from a CDN carries an `integrity` hash and
`crossorigin`, so a tampered CDN file is refused by the browser; bumping one
of those pinned versions means recomputing its hash. The session cookie
(used only for flash messages) is `SameSite=Lax`, and `Secure` whenever
`TRUSTED_PROXY_COUNT` is above 0, i.e. behind the HTTPS edge.

## What is public by design

Two routes need no credential, token, or signature at all, because they
are meant to be reachable by anyone: `GET /healthz` (the container
healthcheck) and `GET /pin` (the web-first entry point that creates a
pending page and redirects to its map). Creating an admin page
(`/pages/new`) is maintainer-only, and the public edge does not route it. An anonymous visitor who reaches these can, within
the rate limits above: create a pending landing page; cause the service to
make USGS and NOAA API calls on their behalf while discovering gauges near a
pin; and provision USGS sites and NOAA gauges as `user`-origin sources tied
to that page. None of that reaches another page, the admin portal, or any
subscriber who has not opted in — and a page abandoned without ever
subscribing anyone is exactly what
[`source-retirement.md`](source-retirement.md) exists to clean back up.

Channel credentials are the one deliberate exception to "secrets live in the
environment, not the database": `telegram_bot_token`, the Twilio SID/token/
numbers, and the Facebook page/verify tokens and app secret are ordinary
`settings` rows, editable from the portal so an operator can rotate or add a
channel without a redeploy. That is safe specifically because reaching the
settings page already requires the admin credentials described above; the
one secret that must never be reachable through the database is the admin
credential itself, since that would let the portal's own protection be
bypassed by whoever could read the table it guards.
