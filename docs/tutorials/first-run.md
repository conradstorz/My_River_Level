# First run

This walks a brand-new deployment from an empty checkout to a phone that
receives its first river alert. It assumes a Docker host you can run
`docker compose` against and a PostgreSQL server reachable from it — any
host, any Postgres, not necessarily the maintainer's own setup described in
[`../howto/this-deployment.md`](../howto/this-deployment.md). The one example
gauge used throughout is USGS site `03294500`, the Ohio River at Louisville.

## 1. Clone the repository

```bash
git clone <repository-url> river-monitor
cd river-monitor
```

## 2. Create the database and role

River Monitor never runs its own database container — it expects a role and
two databases to already exist on a PostgreSQL server it can reach over the
network named in `DATABASE_URL`. Connect to that server with `psql` (as a
superuser) and run:

```sql
CREATE ROLE river WITH LOGIN PASSWORD '<db-password>';
CREATE DATABASE rivermonitor OWNER river;
CREATE DATABASE river_test OWNER river;
```

`init_db` creates every table in `rivermonitor` the first time the app
starts — see [`../reference/database.md`](../reference/database.md) for the
full schema — so nothing further needs to be loaded by hand. `river_test` is
only needed if you plan to run the test suite; see
[`../howto/run-tests.md`](../howto/run-tests.md).

`docker-compose.yml` expects to join an external Docker network named
`shared-db` so the `app` container can reach Postgres by hostname. If you
don't already have that network from another project, create it with
`docker network create shared-db` and attach your PostgreSQL container to it
(`docker network connect shared-db <your-postgres-container>`) — or edit the
`networks:` section of `docker-compose.yml` to point at whatever network your
Postgres server is already on.

## 3. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` and set `DATABASE_URL` and `TEST_DATABASE_URL` to the role and
databases from step 2, using the hostname your Postgres server is reachable
at as `<db-host>`:

```
DATABASE_URL=postgresql://river:<db-password>@<db-host>:5432/rivermonitor
TEST_DATABASE_URL=postgresql://river:<db-password>@<db-host>:5432/river_test
```

Generate a `FLASK_SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

and paste the result into `.env` as `FLASK_SECRET_KEY=<value>`. Every
variable `.env` needs, and what happens if one is missing, is in
[`../reference/environment.md`](../reference/environment.md).

## 4. Set the admin password

The portal is guarded by HTTP Basic auth, and with no password hash set it
returns 503 on every admin route rather than opening up. Generate
`ADMIN_PASSWORD_HASH` with the project's own script rather than typing one by
hand — the escaping it needs to survive `docker compose`'s `.env`
interpolation is easy to get wrong:

```bash
uv run --with werkzeug python set_admin_password.py
```

Full detail on why this needs its own script, and how to rotate the password
later, is in [`../howto/secrets.md`](../howto/secrets.md).

## 5. Start the stack

```bash
docker compose up -d --build
```

Give it a little under a minute, then check:

```bash
docker compose ps
```

Expected: the `app` service shows `healthy`. If it doesn't,
[`../howto/diagnose.md`](../howto/diagnose.md) covers the common causes —
usually a database that isn't reachable yet, or a malformed
`ADMIN_PASSWORD_HASH`.

## 6. Open the portal and log in

Visit `http://<portal-host>:5743/` and log in with `ADMIN_USERNAME` and the
password you set in step 4. Expected: the dashboard, currently empty — no
sites, no notifications.

## 7. Add the example site

Open the Sites page (`http://<portal-host>:5743/sites`), and under "Add Site
by USGS Number" enter `03294500`, an optional name such as "Ohio River at
Louisville", pick **Parameter: Gage height (00065)**, and click **Add**.
Expected: a banner confirming the site was added, and a new row in the table.

Watch it get its first reading:

```bash
docker compose logs -f app
```

Expected: a request against `nwis/iv` for `03294500`, with no accompanying
`produced no reading` warning for that site — that warning is what a failed
fetch looks like, so its absence is the confirmation you want. The site's
health badge on the Sites page turns from blank to **Reporting** once a
successful fetch has landed.

## 8. Create the Telegram bot

Alerts are routed to subscribers, and the easiest way to become one yourself
is through the bot. Creating it with BotFather and pasting its token into the
portal is covered step by step in
[`../howto/telegram-bot.md`](../howto/telegram-bot.md) — follow steps 1
through 4 there now: create the bot, open **Settings → Notification
Channels**, paste the token, and confirm `TelegramAdapter started polling` in
the logs.

While you're in Settings, also set **Public base URL** on **Settings →
Monitoring** to `http://<portal-host>:5743` (or your `https://<portal-host>`
if you're already behind a reverse proxy — see
[`../howto/reverse-proxy.md`](../howto/reverse-proxy.md)). Every map link,
page-editor link, and `/start` reply the bot sends is built from this value,
so alerts and pin links won't work correctly without it.

## 9. Pin a spot on the river

In Telegram, send `/start` to your bot (its username is whatever you gave
BotFather, shown here as `<bot-username>`). Expected: a welcome message
ending in a map link. Open it, drop a pin near the Louisville, Kentucky
stretch of the Ohio River — close to site `03294500` — and save it.
Expected: a confirm screen listing the USGS and NOAA gauges found near the
pin; confirm to save. Back in the chat, expect a message beginning `✓ You're
set up for <river>. You'll hear about:` listing the gauges you're now
subscribed to. See [`../reference/telegram-commands.md`](../reference/telegram-commands.md)
for every other command the bot understands.

## 10. Wait for — or force — a real alert

A real alert fires the next time one of your subscribed gauges' conditions
change: a USGS reading crosses a percentile threshold, its rate of rise or
fall crosses the configured limit, or a NOAA gauge's flood category changes.
None of that is under your control on demand, so to prove the whole pipeline
works without waiting for the river to cooperate, force one:

1. Open **Settings → Alert Thresholds** and lower `high_percentile` well
   below the example site's current reading (a small number like `1` works
   for most sites, since almost every reading exceeds the 1st percentile of
   its own history).
2. Save, then wait one `poll_interval_minutes` (15 minutes by default) for
   `PollingThread` to re-fetch and classify the site.
3. Expected: an alert in Telegram announcing the site is now HIGH (or SEVERE
   HIGH), and a matching row on the dashboard.
4. Restore `high_percentile` to its original value (`90` by default) so
   future alerts reflect real conditions rather than this test.

See [`../explanation/usgs-classification.md`](../explanation/usgs-classification.md)
for exactly how a reading becomes a severity, and
[`../explanation/alert-routing-and-sensitivity.md`](../explanation/alert-routing-and-sensitivity.md)
for how that alert found its way to your chat specifically.

## Where to go next

- Add more sites and build a shareable landing page —
  [`../howto/add-gauges-as-admin.md`](../howto/add-gauges-as-admin.md)
- Wire up SMS, WhatsApp, or Facebook Messenger —
  [`../howto/twilio-and-facebook.md`](../howto/twilio-and-facebook.md)
- Put the portal behind TLS —
  [`../howto/reverse-proxy.md`](../howto/reverse-proxy.md)
- Back up before you rely on this —
  [`../howto/backup-restore.md`](../howto/backup-restore.md)
- Understand who can reach what, and why —
  [`../explanation/security-model.md`](../explanation/security-model.md)
- The full index of every document —
  [`../README.md`](../README.md)
