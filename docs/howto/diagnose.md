# Diagnosing River Monitor

Work top to bottom in each table — the earlier rows are more common than the later ones.

## No alerts arrive

| likely cause | check | fix |
|---|---|---|
| Page's `active` flag is off, or its `status` isn't `active` (`pending`/`paused`/`stopped`) | Admin → Pages (`/admin/pages`) — the Disable/Enable button and the page's lifecycle status | [`../explanation/alert-routing-and-sensitivity.md`](../explanation/alert-routing-and-sensitivity.md) |
| Subscriber isn't active — globally unsubscribed, or `unsubscribed`/`paused` on this page | Subscribers page (`/subscribers`) for global; the page editor's Active Subscribers list for page-scoped | [`add-gauges-as-admin.md`](add-gauges-as-admin.md) |
| Page's sensitivity dial is too low for this alert's kind (e.g. a `floods`-only page won't hear a plain HIGH transition) | Page editor's Alert sensitivity card, or send `/sensitivity` in Telegram | [`../explanation/alert-routing-and-sensitivity.md`](../explanation/alert-routing-and-sensitivity.md) |
| Telegram bot token missing (only affects Telegram recipients) | `docker compose logs app` — look for `Telegram bot token not configured` | [`telegram-bot.md`](telegram-bot.md) |
| Telegram delivery is failing (blocked bot, bad chat id, rate limit) | `docker compose logs app` — look for `Telegram send failed: <error>` | [`#telegram-bot-is-silent`](#telegram-bot-is-silent) |
| The site has simply never crossed a threshold yet (expected for a newly added gauge, not a bug) | `docker compose exec app python -c "from db.models import get_db; c=get_db().cursor(); c.execute('SELECT severity, checked_at FROM site_conditions WHERE site_id=%s ORDER BY id DESC LIMIT 5', (1,)); print(c.fetchall())"` (substitute the real site id) | [`../reference/alerts.md`](../reference/alerts.md) |

## Site shows "Not reporting"

| likely cause | check | fix |
|---|---|---|
| Wrong `parameter_code` — this gauge doesn't report `00060`/`00065` | Sites page (`/sites`) — hover the "Not reporting" badge for `USGS returned no interval data ... (parameter ...)` | [`add-gauges-as-admin.md`](add-gauges-as-admin.md) |
| No daily history since `historical_start_year` — too new a gauge, or the start year is set too far back | Hover the badge for `USGS returned no daily history for site ... since <year>; percentiles cannot be computed` | [`../reference/settings.md`](../reference/settings.md) |
| A USGS API outage or transient network failure | `docker compose logs app` around the last poll — look for a traceback under `Error evaluating site` | [`../reference/threads.md`](../reference/threads.md) |
| `last_error` / `last_error_at` on the site itself | Sites page (`/sites`) — the badge tooltip is exactly this column's value | [`../reference/database.md`](../reference/database.md) |

## NOAA gauge stuck at Unknown or Not yet assessed

| likely cause | check | fix |
|---|---|---|
| NWPS outage, or the gauge has no observed current stage | `docker compose logs app` — look for `NOAA stage fetch failed` (from `monitor/noaa_client.py`) or `NOAA gauge <lid> returned no current stage` (from `monitor/noaa_polling.py`) | Wait for the next poll; verify the LID is correct on water.noaa.gov |
| Flood-category thresholds are missing on this gauge | Open the gauge on the page editor and check which of action/minor/moderate/major stages are filled in | Pick a gauge that publishes flood categories, or accept that it can never classify above its highest available threshold — see [`../explanation/noaa-flood-categories.md`](../explanation/noaa-flood-categories.md) |
| Forecast has never been successfully fetched (`has_forecast` is `NULL`) | Grade badge on the page editor reads "Not yet assessed" | Wait up to `forecast_poll_hours` for `ForecastPollingThread` to check again; see [`../explanation/gauge-quality-grading.md`](../explanation/gauge-quality-grading.md) |

## Telegram bot is silent

| likely cause | check | fix |
|---|---|---|
| `telegram_bot_token` is unset | `docker compose logs app` — `Telegram bot token not configured — waiting for it to be set in Settings` | [`telegram-bot.md`](telegram-bot.md) |
| Token is set but invalid or revoked | `docker compose logs app` — `TelegramAdapter started polling` never appears, or is followed by repeated `Telegram bot failed — restarting` | [`telegram-bot.md`](telegram-bot.md) |
| `public_base_url` is empty — the bot answers with the `NO_BASE_URL` text instead of a link | Settings → Monitoring (`/settings/monitoring`) — Public base URL field | [`../reference/settings.md`](../reference/settings.md) |
| The user blocked or deleted the chat with the bot | `docker compose logs app` — `Telegram send failed: <error>` naming that chat | [`../reference/telegram-commands.md`](../reference/telegram-commands.md) |

## Portal returns 503

| likely cause | check | fix |
|---|---|---|
| Neither `ADMIN_PASSWORD_HASH` nor `ADMIN_PASSWORD` is set | `docker compose logs app` — `Refusing <METHOD> <path>: no admin password configured`; the 503 response body explains it further | [`secrets.md`](secrets.md) |
| `ADMIN_PASSWORD_HASH` is set but malformed | `docker compose logs app` — `ADMIN_PASSWORD_HASH is not a valid werkzeug hash: expected 'method$salt$hash' but found N '$' separator(s)...` | [`#admin-password-hash-rejected`](#admin-password-hash-rejected) |
| A worker thread died, so `/healthz` itself reports unhealthy (this is not the Basic-auth guard) | `curl http://<portal-host>:5743/healthz`, or the CLI one-liner in [`../reference/cli.md`](../reference/cli.md) | [`#container-unhealthy-or-restarting`](#container-unhealthy-or-restarting) |

## Container unhealthy or restarting

| likely cause | check | fix |
|---|---|---|
| A critical worker thread died | `docker compose logs app` — `Worker thread(s) died: [...] — exiting so Docker restarts us` | [`../reference/threads.md`](../reference/threads.md) |
| Database unreachable — wrong `DATABASE_URL`, or the `shared-db` network / shared-postgres server isn't up | `docker compose logs app` — `psycopg2.OperationalError`; then, in the shared-postgres project, `docker compose ps` | [`../reference/environment.md`](../reference/environment.md) |
| `app_logs` volume is still root-owned from a pre-hardening deployment | `docker compose logs app` — a `PermissionError` writing `logs/river_monitor.log` | [`upgrade.md`](upgrade.md) |
| Healthcheck's 30 s start period hasn't elapsed yet | `docker compose ps` — wait and re-check | [`this-deployment.md`](this-deployment.md) |
| A dead non-critical thread (`TelegramAdapter`) leaves `/healthz` at 503 forever — Compose's `restart: unless-stopped` does **not** act on an unhealthy healthcheck by itself, only on the process exiting, and a non-critical thread's death never makes the process exit | `docker compose ps` shows `unhealthy` but the container keeps running, not restarting | `docker compose restart app` — see [`../reference/threads.md`](../reference/threads.md) |

## Pin finds no gauges

| likely cause | check | fix |
|---|---|---|
| The pin is more than 2 km from the nearest NHDPlus flowline (off-network) | `docker compose logs app` — `Pin %s,%s is %.1f km from the nearest flowline` | [`../explanation/pin-discovery.md`](../explanation/pin-discovery.md) |
| NLDI is down or timed out | `docker compose logs app` — `NLDI snap failed for <lat>,<lon>: <error>` | [`../explanation/pin-discovery.md`](../explanation/pin-discovery.md) |
| The USGS parameter check failed, dropping every USGS candidate for this discovery | `docker compose logs app` — `USGS parameter check failed: <error>` | [`../explanation/pin-discovery.md`](../explanation/pin-discovery.md) |
| `discovery_reach_km` is too small for this stretch of river | Settings → Monitoring (`/settings/monitoring`) — "Pin discovery reach along the river (km)" | [`../reference/settings.md`](../reference/settings.md) |

## Pin page stuck pending

| likely cause | check | fix |
|---|---|---|
| The web-first page has a pin saved but no chat has tapped its deep link yet | Admin → Pages (`/admin/pages`) — `status` is `pending` and the page already has gauges | [`telegram-bot.md`](telegram-bot.md) |
| `telegram_bot_username` is empty, so the map's bot link has nothing to point to | Settings → Notification Channels — the bot only records its username once it connects; confirm with the CLI one-liner in [`telegram-bot.md`](telegram-bot.md) | [`telegram-bot.md`](telegram-bot.md) |
| The chat bound a newer page — binding a second page stops the earlier one, so the earlier page's status is `stopped`, not stuck | Send `/mypages` in Telegram | [`../reference/telegram-commands.md`](../reference/telegram-commands.md) |
| The page aged out of the retirement sweep (24 h with no pin, or 7 days pinned but never bound) and no longer exists | Admin → Pages (`/admin/pages`) — no longer listed | [`../explanation/source-retirement.md`](../explanation/source-retirement.md) |

## Alerts stopped after pause or stop

| likely cause | check | fix |
|---|---|---|
| Page `status` is `paused` | Send `/settings` in Telegram, or check Admin → Pages (`/admin/pages`) | [`telegram-bot.md`](telegram-bot.md) |
| A paused page just needs `/resume`, not a new pin | Send `/resume` in Telegram | [`../reference/telegram-commands.md`](../reference/telegram-commands.md) |
| Page `status` is `stopped` | Admin → Pages (`/admin/pages`) shows `stopped` | [`../explanation/alert-routing-and-sensitivity.md`](../explanation/alert-routing-and-sensitivity.md) |
| A stopped page cannot be resumed — it needs a fresh one | Send `/start` in Telegram to set up a new page | [`telegram-bot.md`](telegram-bot.md) |

## Admin password hash rejected

| likely cause | check | fix |
|---|---|---|
| `$` separators were eaten by docker compose interpolation (hash written with a single `$` instead of `$$`) | `docker inspect my_river_level-app-1 --format '{{range .Config.Env}}{{println .}}{{end}}' \| grep ADMIN_PASSWORD_HASH` — fewer than two `$` | [`secrets.md`](secrets.md) |
| A hand-typed `$$` was expanded away by PowerShell before it ever reached `.env` | Same `docker inspect` command | [`secrets.md`](secrets.md) |
| The hash is malformed for some other reason | `docker compose logs app` — the exact `ADMIN_PASSWORD_HASH is not a valid werkzeug hash` line names how many separators it found | [`secrets.md`](secrets.md) |
| Hand-crafting the value instead of generating it | Regenerate: `uv run --with werkzeug python set_admin_password.py` | [`../reference/cli.md`](../reference/cli.md) |

## Tests cannot find tests directory

| likely cause | check | fix |
|---|---|---|
| `tests/` was accidentally added to `.dockerignore` (it must stay out — only `Dockerfile.test.dockerignore` should list it) | `grep tests .dockerignore` | [`run-tests.md`](run-tests.md) |
| The test image was built before `--build` was added to the command, so an older image without `tests/` is being reused | Confirm the run command includes `--build` | [`run-tests.md`](run-tests.md) |
| The test image's build context excludes `tests/` for some other reason | `docker compose -f docker-compose.yml -f docker-compose.test.yml build test` then inspect the built image's `/app/tests` | [`run-tests.md`](run-tests.md) |

## Tests cannot reach the database

| likely cause | check | fix |
|---|---|---|
| Running plain `pytest` from a machine that can't open a TCP connection to the `postgres` service (e.g. a Windows CLI machine driving a remote Docker daemon) | `pytest` — a `psycopg2` connection error rather than a test failure | [`run-tests.md`](run-tests.md) |
| The shared-postgres server isn't running | In the shared-postgres project directory: `docker compose ps` | [`this-deployment.md`](this-deployment.md) |
| `TEST_DATABASE_URL` is wrong or unset | Check `.env`, or `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test env` | [`../reference/environment.md`](../reference/environment.md) |
