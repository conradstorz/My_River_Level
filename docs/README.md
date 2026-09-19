# River Monitor operator manual

River Monitor watches USGS and NOAA river gauges, classifies conditions, and
alerts subscribers over Telegram, SMS, WhatsApp, and Facebook Messenger. This
manual documents the deployed system: how to run it, how to operate it day to
day, and how each piece works underneath.

## Start here

- [First run](tutorials/first-run.md) — clone the repository, provision a
  database, deploy the container, and walk through your first alert end to
  end.

## Tutorials

- [First run](tutorials/first-run.md) — clone the repository, provision a
  database, deploy the container, and walk through your first alert end to
  end.

## How-to guides

Task-oriented instructions for an operator who already has it running.

### Run it

- [This deployment](howto/this-deployment.md) — the maintainer's own
  SSH-remote-Docker, shared-Postgres runbook; a worked example, not the
  generic path.
- [Upgrade](howto/upgrade.md) — pulling new code and rebuilding the container
  without losing data.
- [Backup and restore](howto/backup-restore.md) — what a `pg_dump` of
  `rivermonitor` captures, what it misses, and how to restore it.
- [Rotating secrets](howto/secrets.md) — rotating the database password,
  `FLASK_SECRET_KEY`, admin password, and channel tokens.
- [Reverse proxy](howto/reverse-proxy.md) — why and how to put the portal
  behind a TLS-terminating reverse proxy instead of exposing it directly.
- [Running the test suite](howto/run-tests.md) — running `pytest` against the
  shared PostgreSQL server, in Docker or on the host.

### Connect channels

- [Setting up the Telegram bot](howto/telegram-bot.md) — creating a bot with
  BotFather, wiring its token and `public_base_url` into the portal, and
  pinning a gauge to it from the chat.
- [Setting up Twilio and Facebook Messenger](howto/twilio-and-facebook.md) —
  configuring SMS/WhatsApp and Messenger credentials and webhook secrets on
  the portal Settings page.

### Manage gauges and pages

- [Adding gauges as an admin](howto/add-gauges-as-admin.md) — adding USGS and
  NOAA gauges, building shareable landing pages, and subscribing recipients
  and their alert sensitivity.

### When something is wrong

- [Diagnosing River Monitor](howto/diagnose.md) — symptom-first
  troubleshooting tables, most common cause first.

## Reference

Exhaustive, generated-style lookups.

- [Settings](reference/settings.md) — every key in the `settings` table, its
  default, and which thread re-reads it.
- [Environment variables](reference/environment.md) — every variable the
  container reads from `.env`, and what happens when one is missing.
- [Database](reference/database.md) — every table `init_db` creates and the
  migrations applied on top of it.
- [HTTP routes](reference/http-routes.md) — every route, grouped by how it is
  protected.
- [Telegram commands](reference/telegram-commands.md) — every command and
  callback the bot understands, with its exact reply text.
- [Alerts](reference/alerts.md) — every notification-queue item type, who
  produces it, and who receives it.
- [Threads](reference/threads.md) — every worker thread `main.py` starts and
  what `/healthz` does about it.
- [CLI](reference/cli.md) — commands an operator runs from the project
  directory.

## Explanation

Background on why the system behaves the way it does.

- [Architecture](explanation/architecture.md) — how the daemon threads, the
  database, and the notification queue fit together.
- [USGS classification](explanation/usgs-classification.md) — how one USGS
  reading becomes a percentile, a severity, and a queued alert.
- [NOAA flood categories](explanation/noaa-flood-categories.md) — how a NOAA
  gauge's current stage becomes Action/Minor/Moderate/Major.
- [Gauge quality grading](explanation/gauge-quality-grading.md) — how each
  NOAA gauge's forecast accuracy is scored and turned into a letter grade.
- [Alert routing and sensitivity](explanation/alert-routing-and-sensitivity.md)
  — how an alert reaches only the subscribers of the pages that watch its
  gauge.
- [Pin discovery](explanation/pin-discovery.md) — how a dropped map pin
  becomes a list of nearby USGS and NOAA gauges.
- [Source retirement](explanation/source-retirement.md) — why user-provisioned
  sources get retired automatically and admin-added ones never do.
- [Security model](explanation/security-model.md) — which mechanism guards
  each route, and why each one fails closed rather than open.

## This deployment

[This deployment](howto/this-deployment.md) documents the maintainer's own
setup in full — SSH Docker context, shared PostgreSQL server, and the exact
topology it runs on — as a concrete, worked example once the generic
[tutorial](tutorials/first-run.md) above makes sense.
