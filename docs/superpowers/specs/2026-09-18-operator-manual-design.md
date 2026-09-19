# Operator manual — design

Date: 2026-09-18
Status: approved for planning

## Goal

Give a system operator everything they need to install, configure, run,
upgrade, back up, secure and diagnose River Monitor, and to understand how
its subsystems behave well enough to predict alerts and answer user
questions. Two audiences: a stranger self-hosting on any Docker host, and the
project owner running the known deployment (remote Docker daemon, shared
PostgreSQL). Depth is a reference manual: every setting, table, route,
command, thread and alert type is catalogued.

## Decisions taken during brainstorming

| Question | Decision |
|---|---|
| Audience | Both: generic core docs plus a scrubbed runbook for the known deployment |
| Depth | Reference manual (runbook + internals + exhaustive catalogues) |
| Where the deployment runbook lives | In the repo, scrubbed with placeholders |
| Organisation | Diátaxis: tutorials / howto / reference / explanation |

## Out of scope

Screenshots, a documentation site generator, developer onboarding (CLAUDE.md
remains the developer file), documentation of the external USGS/NOAA
services beyond the calls the code makes.

## 1. Document inventory

```
README.md                          landing page: what it is, 10-line quick start, doc map
docs/README.md                     index of every document below, one line each
docs/tutorials/
  first-run.md                     clean machine -> portal open -> first Telegram alert
docs/howto/
  this-deployment.md               scrubbed runbook for the known deployment
  upgrade.md                       pull, rebuild, recreate, verify /healthz, roll back
  backup-restore.md                pg_dump / restore of the app database
  secrets.md                       set and rotate every secret; $$ escaping of the hash
  telegram-bot.md                  BotFather, token, public_base_url, verify /start
  twilio-and-facebook.md           SMS / WhatsApp / Messenger setup and webhook signatures
  add-gauges-as-admin.md           sites by number/name, NOAA gauges, landing pages, linking
  reverse-proxy.md                 TLS in front of waitress, TRUSTED_PROXY_COUNT, health checks
  diagnose.md                      symptom -> cause -> fix
  run-tests.md                     Docker test overlay, TEST_DB_SUFFIX, no-buildx constraint
docs/reference/
  settings.md                      every settings-table key
  environment.md                   every environment variable
  database.md                      every table and column; migration policy
  http-routes.md                   every route: method, auth class, purpose, rate limit
  telegram-commands.md             every command and inline callback
  alerts.md                        every queue item type, payload, recipients, message format
  threads.md                       every worker thread: interval, reads/writes, failure handling
  cli.md                           compose commands, set_admin_password.py, test commands
docs/explanation/
  architecture.md                  threads + queue + adapters + PostgreSQL, one diagram
  usgs-classification.md           percentiles, thresholds, direction, reminders, rate of change
  noaa-flood-categories.md         stages, categories, observation archive
  gauge-quality-grading.md         forecast archive, MAE at 24/48/72 h, grades, tri-state has_forecast
  alert-routing-and-sensitivity.md per-page routing, the three dials, status lifecycle, direct messages
  pin-discovery.md                 NLDI snap and navigation, parameter check, NOAA pairing, fallback, river name
  source-retirement.md             origin, liveness rule, 24 h / 7 d garbage collection, reactivation
  security-model.md                Basic auth, token URLs, webhook signatures, rate limits, what is public
```

`docs/docker-ssh-access.md` is folded into `howto/this-deployment.md`
(scrubbed) and `reference/environment.md`, then deleted along with
`docs/docker-ssh-access.html`. `docs/plans/` and `docs/superpowers/` are
untouched.

The README shrinks to: one-paragraph description, a quick start of at most
ten lines (copy `.env.example`, fill values, `docker compose up -d --build`,
open the portal, set the Telegram token), and a table linking to
`docs/README.md` and the four folders. Everything else currently in the
README moves to the matching document.

## 2. Content rules

**Source of truth is the code.** Every settings key, environment variable,
table, column, route, Telegram command, queue item type and thread named in a
reference document is read from the code at writing time: `db/models.py`
(`DEFAULT_SETTINGS`, `SCHEMA_STATEMENTS`, `MIGRATION_STATEMENTS`),
`web/routes.py` and `web/health.py` (`@app.route`), `web/auth.py`
(`PUBLIC_ENDPOINTS`), `web/app.py` and `main.py` (environment, threads),
`monitor/adapters/telegram.py` and `telegram_commands.py` (commands),
`monitor/dispatcher.py` (queue item types). Each reference page carries a
final line `Verified against commit <short sha>`.

**Scrubbing.** Placeholders, used consistently across all documents:
`<docker-host>` (remote daemon hostname), `<docker-user>` (SSH user on it),
`<portal-host>` (address users reach the portal at), `<db-host>`,
`<db-password>`, `<bot-username>`. `howto/this-deployment.md` opens with a
box telling the reader to take real values from their `.env` and SSH config.
No file under `docs/` may contain a real hostname, IP address, username,
token, password or chat id. Before commit, a grep for the current LAN
hostname and SSH user must return nothing under `docs/`.

**How-to pages** are numbered steps. Each step is one command or one portal
action, followed by the expected result (output line, status, page). Each
page ends with a "If it went wrong" list of symptoms linking to
`howto/diagnose.md`.

**Tutorial** is a single narrative from a clean Docker host to the first
alert, generic (any Docker daemon, any reachable PostgreSQL), and links to
the how-tos rather than repeating them.

**Explanation pages** answer "why does it behave this way" and name the
function responsible, at most one code reference per paragraph, no code
blocks longer than five lines.

**Reference pages** are tables: one row per item, sorted (alphabetically for
settings, environment and commands; by URL for routes; by table then column
for the database; by thread start order for threads), a one-sentence lead,
no other prose. Columns:

| Page | Columns |
|---|---|
| settings.md | key, type, default, unit, effect, read by |
| environment.md | variable, required, default, read by, notes |
| database.md | table, column, type, meaning, written by |
| http-routes.md | method, path, auth (admin / token / signature / open), purpose, rate limit |
| telegram-commands.md | command or callback, argument, effect, reply |
| alerts.md | type, produced by, payload keys, recipients, message format |
| threads.md | name, interval, reads, writes, on failure, critical for /healthz |
| cli.md | command, purpose, notes |

**Diagnose page** is one table: symptom, likely cause, check, fix, linking
to the reference or explanation page that justifies it. It must cover at
least: no alerts arriving; a site shown "Not reporting"; the Telegram bot
silent; portal returns 503; container unhealthy or restarting; a pin finds no
gauges; a pin page stuck pending; alerts stopped after a pause or stop; the
admin password hash rejected.

**Cross-links** are relative Markdown links. Every how-to links to the
reference and explanation pages it depends on. `docs/README.md` links every
document. `CLAUDE.md` gains one line pointing at `docs/README.md` and is not
otherwise changed.

## 3. Process and verification

- Written on branch `docs/operator-manual`, one sub-agent per folder, each
  given the code files to read, this inventory, and the content rules.
- A final consistency pass, done by one agent with a script, checks:
  every `DEFAULT_SETTINGS` key appears in `reference/settings.md`; every
  `@app.route` path appears in `reference/http-routes.md`; every
  `CREATE TABLE` name appears in `reference/database.md`; every
  `CommandHandler` name appears in `reference/telegram-commands.md`; every
  relative link in `docs/` resolves to an existing file; no placeholder is
  spelled two ways; the scrub grep is clean.
- README rewrite and `docs/docker-ssh-access.*` removal happen last, after
  the documents they point to exist.
- PR to `main` as before.
