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
`edit_token` can add or remove the page's gauges, change its subscribers, set
its sensitivity, and drive the whole pin flow (map, discover, save) for that
one page — full control over that page's own sources and audience, but
no route into any other page or the admin portal. Losing an edit link is
equivalent to losing write access to that one page.

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

The pin routes are the one place an anonymous visitor's click triggers real
third-party API calls (NLDI, NWPS, USGS), so `web/ratelimit.py`'s
`RateLimiter` throttles them: 10 new pending pages per IP per day, 20
discovery searches per token and 60 per IP per hour, and 10 saves per token
per hour. It is explicitly an in-process, per-container brake, not a
distributed quota — good enough to stop one browser hammering an upstream
API, not a guarantee across multiple app replicas. What counts as "an IP" for
these limits depends on `TRUSTED_PROXY_COUNT`: with it at the default of 0,
Flask's `ProxyFix` is not installed at all and every request is attributed to
whatever address made the TCP connection, which behind an unconfigured
reverse proxy is the proxy itself; only a correctly set proxy hop count makes
`X-Forwarded-For` trustworthy, and thus makes the per-IP limits mean anything
at scale (see [`reverse-proxy.md`](../howto/reverse-proxy.md)).

A NOAA or USGS station name is third-party text that ends up rendered back
to visitors — on `page_view.html` and `page_edit.html` it goes through
ordinary Jinja `{{ }}` interpolation, which HTML-escapes it automatically;
on the pin map, where candidate names arrive over `fetch` as JSON rather
than through a server-rendered template, the page's own script escapes each
field by hand before building markup from it. Neither path ever inserts a
gauge name unescaped.

## What is public by design

Three routes need no credential, token, or signature at all, because they
are meant to be reachable by anyone: `GET /healthz` (the container
healthcheck), `GET, POST /pages/new` (the admin-style page-creation form,
open so anyone can start a landing page without portal credentials), and
`GET /pin` (the web-first entry point that creates a pending page and
redirects to its map). An anonymous visitor who reaches these can, within
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
