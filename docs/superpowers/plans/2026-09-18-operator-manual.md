# Operator Manual Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A complete operator manual under `docs/` in Diátaxis layout (tutorial, how-to, reference, explanation), with a scrubbed runbook for the known deployment, a rewritten README landing page, and a script that proves the reference pages match the code.

**Architecture:** Markdown only. A checker script (`scripts/check_docs.py`) is the "test suite": it extracts settings keys, routes, tables, Telegram handlers and environment variables from the code and asserts each appears in its reference page, resolves every relative link, and greps for leaked identifiers. Documents are written folder by folder; the README rewrite and legacy-doc removal come last.

**Tech Stack:** Markdown, Python 3.11 (checker script, stdlib only), git.

Spec: `docs/superpowers/specs/2026-09-18-operator-manual-design.md`

## Global Constraints

- **Branch:** all work on `docs/operator-manual`, created from `main`.
- **Source of truth is the code.** Reference facts are read at writing time from: `db/models.py` (`DEFAULT_SETTINGS`, `SCHEMA_STATEMENTS`, `MIGRATION_STATEMENTS`), `web/routes.py`, `web/health.py`, `web/auth.py` (`PUBLIC_ENDPOINTS`), `web/app.py`, `main.py`, `monitor/adapters/telegram.py`, `monitor/adapters/telegram_commands.py`, `monitor/dispatcher.py`, `monitor/scheduler.py`, `monitor/retirement.py`, `monitor/pin_discovery.py`, `monitor/polling.py`, `monitor/noaa_polling.py`, `monitor/forecast_polling.py`, `monitor/gauge_quality.py`, `monitor/trend.py`, `monitor/noaa_client.py`, `docker-compose.yml`, `docker-compose.test.yml`, `Dockerfile`, `Dockerfile.test`, `.env.example`, `set_admin_password.py`.
- **Every reference page ends with the line** `Verified against commit <short sha>` where `<short sha>` is `git rev-parse --short HEAD` at writing time.
- **Placeholders, spelled exactly:** `<docker-host>`, `<docker-user>`, `<portal-host>`, `<db-host>`, `<db-password>`, `<bot-username>`. Nothing under `docs/` may contain a real hostname, IP address (other than `localhost`/`127.0.0.1`/`0.0.0.0`/the documentation example `192.168.1.50`), username, token, password, or chat id. The checker greps for the strings in `scripts/check_docs.py` `FORBIDDEN` and must pass.
- **Page shapes:** how-to = numbered steps, one command or portal action per step, expected result after each, ending in an "If it went wrong" list linking to `howto/diagnose.md`; reference = one-sentence lead + sorted table(s) with the exact columns in the spec; explanation = prose answering "why", at most one code reference per paragraph, no code block over five lines.
- **Links** are relative Markdown links (`../reference/settings.md`), never absolute paths or GitHub URLs to files in this repo.
- **Environment variables the app reads** (for `reference/environment.md`): `DATABASE_URL`, `TEST_DATABASE_URL`, `TEST_DB_SUFFIX` (compose only), `FLASK_SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_PASSWORD_HASH`, `TRUSTED_PROXY_COUNT`.
- **Settings keys** (26): `poll_interval_minutes low_percentile high_percentile very_low_percentile very_high_percentile reminder_low_high_hours reminder_severe_hours historical_start_year search_radius_miles telegram_bot_token twilio_account_sid twilio_auth_token twilio_sms_number twilio_whatsapp_number facebook_page_token facebook_verify_token facebook_app_secret rate_change_threshold_ft rate_change_threshold_pct rate_change_window_hours rate_change_min_interval_hours site_stale_hours forecast_poll_hours discovery_reach_km public_base_url telegram_bot_username`.
- **Telegram handlers** (10 commands + 1 callback pattern): `start subscribe unsubscribe mypages settings sensitivity sources pause resume stop`, callback `^(sens|rm):`.
- **Checker runs locally without Docker:** `python scripts/check_docs.py` (the script imports nothing from the project; it parses source files as text).
- **Commit after every task**, message ending `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Do not touch `docs/plans/`, `docs/superpowers/`, or any code file. `CLAUDE.md` gets exactly one added line (Task 7).

## File Structure

| Path | Task | Responsibility |
|---|---|---|
| `scripts/check_docs.py` | 1 | Consistency checker (the test suite for this plan) |
| `docs/reference/*.md` (8) | 2 | Exhaustive catalogues |
| `docs/explanation/*.md` (8) | 3 | How each subsystem behaves and why |
| `docs/howto/this-deployment.md`, `upgrade.md`, `backup-restore.md`, `secrets.md`, `reverse-proxy.md`, `run-tests.md` | 4 | Infrastructure procedures |
| `docs/howto/telegram-bot.md`, `twilio-and-facebook.md`, `add-gauges-as-admin.md`, `diagnose.md` | 5 | Product procedures and the diagnosis table |
| `docs/tutorials/first-run.md`, `docs/README.md` | 6 | Narrative tutorial and the index |
| `README.md`, `CLAUDE.md` (+1 line), delete `docs/docker-ssh-access.md` and `.html` | 7 | Landing page and cleanup; final checker run |

---

### Task 1: Branch and the docs checker

**Files:**
- Create: `scripts/check_docs.py`

**Interfaces:**
- Produces: `python scripts/check_docs.py` exits 0 when every check passes, 1 otherwise, printing one line per failure prefixed `FAIL:` and a final `OK` or `N failure(s)`. Checks: (1) every `DEFAULT_SETTINGS` key appears as a table cell `` `key` `` in `docs/reference/settings.md`; (2) every `@app.route("...")` path appears as `` `path` `` in `docs/reference/http-routes.md`; (3) every `CREATE TABLE IF NOT EXISTS name` appears as `` `name` `` in `docs/reference/database.md`; (4) every `CommandHandler("name"` appears as `` `/name` `` in `docs/reference/telegram-commands.md`; (5) every variable in `ENV_VARS` appears as `` `VAR` `` in `docs/reference/environment.md`; (6) every relative Markdown link in `docs/**/*.md` and `README.md` resolves to an existing file (anchors stripped); (7) no forbidden identifier appears under `docs/`; (8) every file in the spec inventory exists; (9) each `docs/reference/*.md` ends with `Verified against commit `.

- [ ] **Step 1: Create the branch**

```bash
git checkout -b docs/operator-manual main
```

- [ ] **Step 2: Write the checker**

```python
#!/usr/bin/env python
"""Consistency checks for the operator manual under docs/.

Run: python scripts/check_docs.py
Exit 0 when every check passes. The script reads source files as text so it
needs no dependencies and no database.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

ENV_VARS = [
    "DATABASE_URL", "TEST_DATABASE_URL", "TEST_DB_SUFFIX", "FLASK_SECRET_KEY",
    "ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_PASSWORD_HASH", "TRUSTED_PROXY_COUNT",
]

# Real identifiers from the known deployment that must never appear in docs.
FORBIDDEN = ["hpz440", "gte@", "conradstorz", "conrad@", "Conrad"]

INVENTORY = [
    "README.md",
    "tutorials/first-run.md",
    "howto/this-deployment.md", "howto/upgrade.md", "howto/backup-restore.md",
    "howto/secrets.md", "howto/telegram-bot.md", "howto/twilio-and-facebook.md",
    "howto/add-gauges-as-admin.md", "howto/reverse-proxy.md", "howto/diagnose.md",
    "howto/run-tests.md",
    "reference/settings.md", "reference/environment.md", "reference/database.md",
    "reference/http-routes.md", "reference/telegram-commands.md", "reference/alerts.md",
    "reference/threads.md", "reference/cli.md",
    "explanation/architecture.md", "explanation/usgs-classification.md",
    "explanation/noaa-flood-categories.md", "explanation/gauge-quality-grading.md",
    "explanation/alert-routing-and-sensitivity.md", "explanation/pin-discovery.md",
    "explanation/source-retirement.md", "explanation/security-model.md",
]

failures = []


def fail(msg):
    failures.append(msg)
    print("FAIL:", msg)


def read(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def check_items(label, items, doc, fmt):
    text = read(DOCS / doc)
    if not text:
        fail(f"{doc} is missing")
        return
    for item in items:
        if fmt.format(item) not in text:
            fail(f"{label} {item!r} not documented in {doc}")


def settings_keys():
    src = read(ROOT / "db" / "models.py")
    block = src.split("DEFAULT_SETTINGS = {", 1)[1].split("\n}", 1)[0]
    return re.findall(r'^\s*"([a-z_]+)":', block, re.M)


def routes():
    out = []
    for name in ("web/routes.py", "web/health.py"):
        out += re.findall(r'@app\.route\("([^"]+)"', read(ROOT / name))
    return out


def tables():
    return re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", read(ROOT / "db" / "models.py"))


def commands():
    out = []
    for name in ("monitor/adapters/telegram.py", "monitor/adapters/telegram_commands.py"):
        out += re.findall(r'CommandHandler\("([a-z]+)"', read(ROOT / name))
    return out


def check_links():
    files = list(DOCS.rglob("*.md")) + [ROOT / "README.md"]
    link = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for md in files:
        if "superpowers" in md.parts or "plans" in md.parts:
            continue
        for target in link.findall(read(md)):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            rel = target.split("#", 1)[0]
            if not rel:
                continue
            if not (md.parent / rel).resolve().exists():
                fail(f"{md.relative_to(ROOT)} links to missing {target}")


def check_forbidden():
    for md in DOCS.rglob("*.md"):
        if "superpowers" in md.parts or "plans" in md.parts:
            continue
        text = read(md)
        for word in FORBIDDEN:
            if word in text:
                fail(f"{md.relative_to(ROOT)} contains forbidden identifier {word!r}")


def check_inventory():
    for rel in INVENTORY:
        if not (DOCS / rel).exists():
            fail(f"missing document docs/{rel}")


def check_verified_lines():
    for md in (DOCS / "reference").glob("*.md"):
        if "Verified against commit " not in read(md).strip().splitlines()[-1]:
            fail(f"{md.relative_to(ROOT)} lacks a final 'Verified against commit' line")


def main():
    check_inventory()
    check_items("setting", settings_keys(), "reference/settings.md", "`{}`")
    check_items("route", routes(), "reference/http-routes.md", "`{}`")
    check_items("table", tables(), "reference/database.md", "`{}`")
    check_items("command", commands(), "reference/telegram-commands.md", "`/{}`")
    check_items("env var", ENV_VARS, "reference/environment.md", "`{}`")
    check_links()
    check_forbidden()
    check_verified_lines()
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Run it to see the expected failures**

Run: `python scripts/check_docs.py`
Expected: 28 `FAIL: missing document ...` lines plus `FAIL: reference/*.md is missing` lines, then `N failure(s)`, exit code 1. (The forbidden-word check passes because `docs/docker-ssh-access.md` contains none of the words; confirm no `contains forbidden identifier` line appears.)

- [ ] **Step 4: Commit**

```bash
git add scripts/check_docs.py
git commit -m "docs: add operator-manual consistency checker"
```

---

### Task 2: Reference pages

**Files:**
- Create: `docs/reference/settings.md`, `environment.md`, `database.md`, `http-routes.md`, `telegram-commands.md`, `alerts.md`, `threads.md`, `cli.md`

**Interfaces:**
- Consumes: `scripts/check_docs.py` from Task 1.
- Produces: the eight pages; later tasks link to them by these exact paths and to these anchors: `settings.md` has one `## ` heading per settings group as shown on the portal Settings page (`Monitoring`, `Alert Thresholds`, `Notification Channels`, plus `## Rate of change` and `## Data health` if the portal groups them so — read `SETTINGS_GROUPS` in `web/routes.py`); `http-routes.md` has headings `## Portal (admin)`, `## Landing pages (token)`, `## Pin onboarding (token)`, `## Webhooks (signature)`, `## Open`; `database.md` has one `## table_name` heading per table.

- [ ] **Step 1: Extract the facts**

Run each and keep the output open while writing:

```bash
git rev-parse --short HEAD
sed -n '/^DEFAULT_SETTINGS = {/,/^}/p' db/models.py
sed -n '/^SCHEMA_STATEMENTS = \[/,/^\]/p' db/models.py
sed -n '/^MIGRATION_STATEMENTS = \[/,/^\]/p' db/models.py
grep -n "@app.route" -A 2 web/routes.py web/health.py
sed -n '/^PUBLIC_ENDPOINTS/,/^})/p' web/auth.py
grep -n "SETTINGS_GROUPS = \[" -A 60 web/routes.py
grep -n "CommandHandler\|CallbackQueryHandler" monitor/adapters/telegram.py monitor/adapters/telegram_commands.py
grep -n "item\[\"type\"\] ==\|\"type\": \"" monitor/dispatcher.py monitor/polling.py monitor/noaa_polling.py monitor/scheduler.py web/routes.py
grep -n "class .*Thread\|INTERVAL\|_SECONDS\|_HOURS\|CRITICAL_THREADS" -A 1 main.py monitor/*.py monitor/adapters/telegram.py
grep -n "RateLimiter(" web/routes.py
```

- [ ] **Step 2: Write `settings.md`**

Lead sentence: "Every key in the `settings` table, seeded with these defaults by `init_db` and editable on the portal Settings page; the thread named in *Read by* re-reads the value on its next cycle, so changes take effect without a restart." One `## ` heading per portal settings group, each a table with columns `key | type | default | unit | effect | read by`. Include every one of the 26 keys from Global Constraints. For each key, *effect* is one sentence in plain words (e.g. `very_high_percentile`: "A reading at or above this percentile of the site's daily history since `historical_start_year` is SEVERE HIGH."). *Read by* names the thread or route (`PollingThread`, `NoaaPollingThread`, `ForecastPollingThread`, `SchedulerThread`, `NotificationDispatcher`, `TelegramAdapter`, `web: /pin/*`, `web: /sites/search`). Credentials (`telegram_bot_token`, `twilio_*`, `facebook_*`) go in the Notification Channels section with *type* `secret`. End with `Verified against commit <sha>`.

- [ ] **Step 3: Write `environment.md`**

Lead sentence: "Variables read from the container environment (compose passes them from `.env`); everything else is a database setting." Table columns `variable | required | default | read by | notes`. Rows for all eight variables in Global Constraints. Notes must state: `ADMIN_PASSWORD_HASH` is a werkzeug `method$salt$hash` and every `$` must be written `$$` in `.env` (link `../howto/secrets.md`); `ADMIN_PASSWORD` is the plaintext fallback hashed on first use; with neither set every admin route returns 503; `TRUSTED_PROXY_COUNT` is the number of reverse proxies whose `X-Forwarded-For` to trust, 0 disables `ProxyFix` (link `../howto/reverse-proxy.md`); `TEST_DB_SUFFIX` is appended to the test database name by `docker-compose.test.yml`. End with the verified line.

- [ ] **Step 4: Write `database.md`**

Lead sentence: "Every table `init_db` creates, then the additive migrations it applies on every start; `init_db` is safe to re-run." One `## table` heading per `CREATE TABLE` (13 tables), each a table with columns `column | type | meaning | written by`. Columns added by `MIGRATION_STATEMENTS` are listed under their table with `(migration)` after the type. Cover the CHECK constraints in *meaning* (`user_pages.status`: pending, active, paused, stopped; `user_pages.sensitivity`: floods, unusual, all; `sites.origin` / `noaa_gauges.origin`: admin, user; `noaa_gauges.severity`; `page_subscribers.status`). Note the deliberate tri-state of `noaa_gauges.has_forecast` (NULL = never successfully checked). Close with a `## Migration policy` paragraph: additive only, `ADD COLUMN IF NOT EXISTS`, never drop or rewrite; and the `## Indexes` list. End with the verified line.

- [ ] **Step 5: Write `http-routes.md`**

Lead sentence: "Every HTTP route, grouped by how it is protected." Headings and membership are decided by `PUBLIC_ENDPOINTS` in `web/auth.py`: a route whose endpoint name is in that set is not admin. Columns `method | path | auth | purpose | rate limit`. *auth* values exactly: `admin` (HTTP Basic), `token` (unguessable UUID in the URL), `signature` (provider webhook signature; fail closed when the secret is unset), `open`. Rate-limit cells come from the `RateLimiter(...)` constructors and which routes call them: `/pin` 10 per IP per day; `/pin/<edit_token>/discover` 20 per token and 60 per IP per hour; `/pin/<edit_token>/save` 10 per token per hour, max 30 sources; all others `—`. Include every one of the 33 paths listed by the extraction grep (32 in `routes.py` + `/healthz`). End with the verified line.

- [ ] **Step 6: Write `telegram-commands.md`**

Lead sentence: "Commands the bot understands; a chat owns at most one live pin page." Table columns `command or callback | argument | effect | reply`. Rows: `/start` (no argument: create or return the chat's pin page and send the map link; `<edit_token>`: bind a web-first page), `/subscribe` (`<page code>` or none), `/unsubscribe`, `/mypages`, `/settings`, `/sensitivity`, `/sources`, `/pause`, `/resume`, `/stop`, callback `sens:<level>`, callback `rm:usgs:<site_id>` / `rm:noaa:<gauge_id>`. Reply texts copied from the constants and f-strings in `telegram_commands.py` (`NO_BASE_URL`, `NO_PAGE`, `TOKEN_NOT_FOUND`, the pause/resume/stop replies). Add a closing paragraph: replies that need a link require `public_base_url`; the bot stores its username in `telegram_bot_username` at startup for the deep link. End with the verified line.

- [ ] **Step 7: Write `alerts.md`**

Lead sentence: "Every item type that travels on the notification queue, who produces it, and who receives it." Columns `type | produced by | payload keys | recipients | message format`. Rows: `transition`, `trend`, `reminder`, `noaa_transition`, `broadcast`, `direct`. Payload keys copied from the dict literals in `polling.py` (`fetch_and_evaluate_site`, `evaluate_trend`), `noaa_polling.py`, `scheduler.py` (`_check_reminders`), `web/routes.py` (`broadcast`, `pin_save`). Recipients: page subscribers of live pages referencing the site/gauge, filtered by `alert_allowed` (link `../explanation/alert-routing-and-sensitivity.md`); `broadcast` → global `subscribers` table; `direct` → one chat. Message format: paste the exact format strings from `dispatcher.py` `format_*_message` as fenced examples with sample values. Add a `## Severity vocabulary` table: USGS `SEVERE LOW / LOW / NORMAL / HIGH / SEVERE HIGH / UNKNOWN`; NOAA `Unknown / Normal / Action / Minor / Moderate / Major`. End with the verified line.

- [ ] **Step 8: Write `threads.md`**

Lead sentence: "Worker threads `main.py` starts, in start order, and what `/healthz` does about them." Columns `name | interval | reads | writes | on failure | critical`. Rows: `TelegramAdapter` (supervisor: polls `telegram_bot_token` every 30 s, restarts the bot on change; not critical), `PollingThread` (`poll_interval_minutes`), `NoaaPollingThread` (`poll_interval_minutes`), `ForecastPollingThread` (`forecast_poll_hours`), `SchedulerThread` (reminders every 300 s, retirement sweep every 3600 s, failed sweep retried next pass), `NotificationDispatcher` (blocking queue get, 1 s timeout), `WebThread` (waitress, 8 threads, port 5743). *on failure* states what the loop does with an exception (log and retry next interval). *critical* is Yes for names in `CRITICAL_THREADS` in `main.py`; explain in one closing paragraph that the supervisor checks every 60 s and exits non-zero so Docker restarts the container, and that `/healthz` returns 503 if any registered thread is dead. End with the verified line.

- [ ] **Step 9: Write `cli.md`**

Lead sentence: "Commands an operator runs, all from the project directory." Sections `## Run`, `## Deploy / upgrade`, `## Tests`, `## Secrets`, `## Database`. Table columns `command | purpose | notes`. Include: `docker compose up -d --build`, `docker compose down`, `docker compose logs -f app`, `docker compose ps`, `docker compose restart app`, the test overlay command with and without a pytest path, `TEST_DB_SUFFIX=_x docker compose ...`, `uv run --with werkzeug python set_admin_password.py` (read the script's docstring for what it prints and how to paste it into `.env`), `docker compose exec app python -c "..."` one-liners for checking `/healthz` and reading a setting, `pg_dump`/`psql` commands used by `../howto/backup-restore.md` (parameterised with `<db-host>`). Note the no-chaining rule is a Claude Code convention, not a shell requirement, so do not mention it. End with the verified line.

- [ ] **Step 10: Run the checker**

Run: `python scripts/check_docs.py`
Expected: no `not documented`, `is missing`, or `lacks a final` lines for `docs/reference/*`; remaining failures are only `missing document` for folders not yet written and links from reference pages to those not-yet-written pages (list them; they are resolved by Tasks 3–6).

- [ ] **Step 11: Commit**

```bash
git add docs/reference
git commit -m "docs: reference pages — settings, environment, database, routes, commands, alerts, threads, cli"
```

---

### Task 3: Explanation pages

**Files:**
- Create: `docs/explanation/architecture.md`, `usgs-classification.md`, `noaa-flood-categories.md`, `gauge-quality-grading.md`, `alert-routing-and-sensitivity.md`, `pin-discovery.md`, `source-retirement.md`, `security-model.md`

**Interfaces:**
- Consumes: reference pages from Task 2 (link to them for every number or name rather than repeating tables).
- Produces: the eight pages with these headings, which how-tos link to by anchor: `usgs-classification.md` → `## Percentiles`, `## Severity bands`, `## Direction`, `## Reminders`, `## Rise and fall alerts`; `pin-discovery.md` → `## Snapping`, `## Walking the stem`, `## Parameter check`, `## Pairing NOAA gauges`, `## Fallback`, `## River name`; `source-retirement.md` → `## What counts as live`, `## Garbage collection`; `security-model.md` → `## What is public by design`.

- [ ] **Step 1: Write `architecture.md`**

Prose (300–500 words) plus one ASCII diagram (≤ 20 lines) showing: three pollers and the scheduler feeding `notification_queue`; the dispatcher reading it and calling adapters; Flask under waitress serving the portal and webhooks; PostgreSQL shared by all; `main.py` supervising. Explain why settings live in the database (hot reload without restart) and why admin credentials do not (the database is what the portal protects). Cite `main.py` once, `monitor/dispatcher.py` once.

- [ ] **Step 2: Write `usgs-classification.md`**

Explain, citing `monitor/polling.py` `fetch_and_evaluate_site` once and `classify_condition` once: the percentile is the fraction of daily-mean values since `historical_start_year` that are below the current reading; the five bands from the four percentile settings; `UNKNOWN` when no percentile; why a first reading never announces a transition (`detect_transition`); direction (`RISING`/`FALLING`/`STEADY`) compared to the previous reading; reminders (`SchedulerThread`, `reminder_low_high_hours` / `reminder_severe_hours`, last `reminder` row in `notifications`); rise/fall alerts from `monitor/trend.py` (window, threshold in ft for gage height or percent for discharge, minimum interval). One worked example with numbers.

- [ ] **Step 3: Write `noaa-flood-categories.md`**

Explain, citing `monitor/noaa_client.py` `classify_noaa_condition` once: stage thresholds action/minor/moderate/major from NWPS, category chosen as the highest threshold met, `Unknown` when stage is missing, missing upper thresholds; every observation archived in `noaa_observations` so forecasts can be graded later; a `noaa_transition` only on category change; gauges polled once regardless of how many pages reference them; inactive gauges skipped.

- [ ] **Step 4: Write `gauge-quality-grading.md`**

Explain, citing `monitor/gauge_quality.py` once and `monitor/forecast_polling.py` once: forecasts archived every `forecast_poll_hours`; pairing forecast points with the observation nearest the same valid time; mean absolute error at 24/48/72 h horizons; the letter-grade thresholds exactly as in the code; the plain-English headline/detail split; why `has_forecast` is tri-state and why a failed fetch never sets it FALSE; "Unrated / Not yet assessed" as the normal state for a new gauge.

- [ ] **Step 5: Write `alert-routing-and-sensitivity.md`**

Explain, citing `db/models.py` `get_page_subscribers_for_site` once, `monitor/scheduler.py` `alert_allowed` once, `monitor/dispatcher.py` once: alerts go only to active subscribers of live pages that link the site or gauge; live = `active=1 AND status='active'`; the `subscribers` table is broadcast-only; the three dials with the exact admission table (floods: NOAA category changes, SEVERE HIGH transitions and their all-clear back to NORMAL, SEVERE HIGH reminders; unusual: adds HIGH, LOW, SEVERE LOW and the return to NORMAL; all: adds rise/fall); admin-created pages default to `all`, pin pages to `unusual`; the status lifecycle pending → active ↔ paused → stopped and what each means for alerts and sources; `direct` messages (confirmation after save); why filtering at dispatch means one poll serves every page.

- [ ] **Step 6: Write `pin-discovery.md`**

Explain, citing `monitor/pin_discovery.py` `discover` once and `monitor/noaa_client.py` `gauges_near` once, with the six headings from Interfaces: NLDI `comid/position` snap and the 2 km off-network threshold; `navigation/UM|DM/nwissite` for `discovery_reach_km`; the parameter check via `seriesCatalogOutput` preferring 00065 then 00060, and that a failed check drops USGS candidates rather than guessing; pairing through the NWPS per-gauge endpoint because the listing has no `usgsId`, inherited tags, "nearby" for the rest; bounding-box fallback tagged "nearest" via `search_radius_miles`; river name derived from station names because NLDI publishes none (the marker and abbreviation rules in one sentence each, with the "Ohio R US OF MCALPINE DAM" → "Ohio River" example); sort order. Note the live-verified facts and the date.

- [ ] **Step 7: Write `source-retirement.md`**

Explain, citing `monitor/retirement.py` `sweep` once: `origin` admin vs user; a page is live when active or paused, or pending with a pin; user sources with no live reference are deactivated (never deleted) once older than 10 minutes; admin rows never touched; re-selection reactivates; pending pages deleted after 24 h without a pin or 7 days with one; the sweep runs hourly from `SchedulerThread` and retries on the next pass after a failure; the "what the Sites page shows" note (`page_count` counts active/paused only).

- [ ] **Step 8: Write `security-model.md`**

Explain, citing `web/auth.py` once, `web/routes.py` once, `web/ratelimit.py` once: HTTP Basic for everything not in `PUBLIC_ENDPOINTS`, failing closed (503) when no password is configured or the hash is malformed; token URLs (`/view`, `/edit`, `/pin`) as unguessable UUID capabilities and what possessing one allows; webhook signature verification for Twilio and Facebook, failing closed when the secret is unset; rate limits on the pin routes and why they are per process; `TRUSTED_PROXY_COUNT`; template escaping of third-party gauge names; `## What is public by design` listing the open endpoints and what an anonymous visitor can cause (a pending page, third-party API calls within the limits, provisioning of sources that retire when unreferenced); secrets never in the database except channel tokens, and why.

- [ ] **Step 9: Run the checker and commit**

Run: `python scripts/check_docs.py`
Expected: no failures mentioning `docs/explanation/`; remaining failures only for howto/tutorial/index pages not yet written.

```bash
git add docs/explanation
git commit -m "docs: explanation pages — architecture, classification, grading, routing, discovery, retirement, security"
```

---

### Task 4: Infrastructure how-tos

**Files:**
- Create: `docs/howto/this-deployment.md`, `upgrade.md`, `backup-restore.md`, `secrets.md`, `reverse-proxy.md`, `run-tests.md`

**Interfaces:**
- Consumes: `docs/reference/environment.md`, `cli.md`, `threads.md`, `database.md`; `docs/explanation/security-model.md`; the existing `docs/docker-ssh-access.md` (source material to fold into `this-deployment.md`; do not delete it in this task).
- Produces: the six pages. Every page ends with `## If it went wrong` — a bullet list of symptoms, each linking to `diagnose.md#<anchor>` (anchors are the lower-case, hyphenated symptom headings Task 5 defines: `#no-alerts-arrive`, `#site-shows-not-reporting`, `#telegram-bot-is-silent`, `#portal-returns-503`, `#container-unhealthy-or-restarting`, `#pin-finds-no-gauges`, `#pin-page-stuck-pending`, `#alerts-stopped-after-pause-or-stop`, `#admin-password-hash-rejected`, `#tests-cannot-find-tests-directory`, `#tests-cannot-reach-the-database`).

Facts to state correctly (verified in code, contradicting older README text): channel credentials (Telegram token, Twilio, Facebook) are **database settings edited on the portal Settings page only**; the commented `TELEGRAM_BOT_TOKEN` / `TWILIO_*` / `FACEBOOK_*` lines in `.env.example` are not read by the application. Do not document them as environment variables.

- [ ] **Step 1: Write `this-deployment.md`**

Open with a boxed note: "This runbook describes the maintainer's own setup. Replace `<docker-host>`, `<docker-user>`, `<portal-host>`, `<db-host>`, `<db-password>` with the values in your `.env` and `~/.ssh/config`." Sections, each numbered steps with expected results:
1. `## Topology` — one paragraph and a ≤ 12-line diagram: Windows CLI machine → SSH → `<docker-host>` running the Docker daemon; the `shared-db` external network; the `shared-postgres` project's server at hostname `postgres` inside Docker; River Monitor's `app` container publishing 5743.
2. `## Docker context` — fold `docs/docker-ssh-access.md` Parts 2–5 (key setup, `docker context create <name> --docker "host=ssh://<docker-user>@<docker-host>"`, `docker context use`, `~/.ssh/config` alias) into steps; keep its security warning verbatim; reduce Part 6 (Portainer) to a two-line mention.
3. `## Shared PostgreSQL` — start it once in the shared-postgres project (`docker compose up -d`), the `river` role, databases `rivermonitor` and `river_test`, and that the `river` password in this project's `.env` must equal `RIVER_DB_PASSWORD` there.
4. `## Environment file` — `cp .env.example .env`, fill `DATABASE_URL`, `TEST_DATABASE_URL`, `FLASK_SECRET_KEY` (generator one-liner from `.env.example`), then `secrets.md` for the admin hash.
5. `## Deploy` — `docker compose up -d --build`, then `docker compose ps` showing `healthy` within about 60 s, then `docker compose logs --tail 30 app` showing every thread started.
6. `## Where things are` — logs in the `app_logs` named volume (`docker compose logs`, or `docker run --rm -v my_river_level_app_logs:/logs alpine tail /logs/river_monitor.log`), the portal at `http://<portal-host>:5743`, the Settings page for channel credentials and `public_base_url`.
7. `## Constraints of this setup` — no host bind mounts (remote daemon); no buildx on the CLI machine so `tests/` stays in the build context; one command at a time when driving Docker from Claude Code.
Close with `## If it went wrong`.

- [ ] **Step 2: Write `upgrade.md`**

Steps: pull `main`; `docker compose up -d --build`; watch `docker compose ps` until `healthy`; read the first 40 log lines for `River Monitor starting` and no `ERROR`; confirm migrations with a `docker compose exec app python -c` one-liner that lists `information_schema.columns` for `user_pages` and shows the newest column; `## Rolling back` — `git checkout <previous sha>` and rebuild (migrations are additive so an older image runs against the newer schema); `## First upgrade from a pre-hardening deployment` — the `app_logs` chown one-liner and the mandatory `ADMIN_PASSWORD_HASH`, copied from the current README. Close with `## If it went wrong`.

- [ ] **Step 3: Write `backup-restore.md`**

Steps: what is in the database (link `../reference/database.md`) and what is not (logs, `.env`); run `pg_dump` through the shared-postgres project's container: `docker exec <postgres-container> pg_dump -U river -Fc rivermonitor > rivermonitor-$(date +%F).dump` (state that the container name comes from `docker ps` in that project); verify with `pg_restore --list`; restore into a fresh database with `pg_restore -U river -d rivermonitor --clean --if-exists`; what to expect after restore (pollers resume, no alerts re-sent because `site_conditions` history is restored with it); a `## Schedule` section suggesting a daily cron on the Docker host with 14-day retention (generic crontab line). Close with `## If it went wrong`.

- [ ] **Step 4: Write `secrets.md`**

Sections per secret with steps: `DATABASE_URL`/`TEST_DATABASE_URL` (rotate the `river` password in shared-postgres, update both `.env` files, recreate the container); `FLASK_SECRET_KEY` (generate, set, recreate; sessions are invalidated); `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` (run `uv run --with werkzeug python set_admin_password.py`, explain the `$$` escaping and the PowerShell trap in two sentences taken from the script docstring, verify with the `docker inspect ... | grep ADMIN_PASSWORD_HASH` line from `.env.example`, the startup log message when it is malformed); channel tokens (Telegram, Twilio, Facebook app secret) are rotated on the portal Settings page and take effect within 30 s for Telegram and immediately for the others; `## Never commit` list. Close with `## If it went wrong`.

- [ ] **Step 5: Write `reverse-proxy.md`**

Steps: why (TLS, one hostname, the portal must not be exposed plain); a minimal nginx server block proxying `https://<portal-host>` to `http://<docker-host>:5743` with `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` and `X-Forwarded-Proto`; set `TRUSTED_PROXY_COUNT=1` in `.env` and recreate; verify with two requests carrying different `X-Forwarded-For` values against `/pin` and observing separate rate-limit buckets (`curl -I`); set `public_base_url=https://<portal-host>` in Settings; webhook URLs for Twilio and Facebook become `https://<portal-host>/webhook/...`; a `## Health checks from the proxy` note pointing at `/healthz` (open, no auth). Close with `## If it went wrong`.

- [ ] **Step 6: Write `run-tests.md`**

Steps: prerequisite shared-postgres up; the overlay command for the whole suite and for one file; `TEST_DB_SUFFIX` for parallel runs; `--build` is mandatory because `Dockerfile.test` copies the source at build time; the classic-builder constraint (`tests/` must stay out of `.dockerignore`; `Dockerfile` strips it from the runtime image); typical duration (whole suite about 9 minutes); reading failures; `## Plain pytest` — only works with a reachable PostgreSQL in `TEST_DATABASE_URL`. Close with `## If it went wrong`.

- [ ] **Step 7: Run the checker and commit**

Run: `python scripts/check_docs.py`
Expected: no failures mentioning these six files except links to `diagnose.md` anchors on a file not yet written (Task 5); no forbidden identifiers.

```bash
git add docs/howto
git commit -m "docs: infrastructure how-tos — deployment runbook, upgrade, backup, secrets, proxy, tests"
```

---

### Task 5: Product how-tos and the diagnosis table

**Files:**
- Create: `docs/howto/telegram-bot.md`, `twilio-and-facebook.md`, `add-gauges-as-admin.md`, `diagnose.md`

**Interfaces:**
- Consumes: reference and explanation pages; the anchor list from Task 4.
- Produces: `diagnose.md` with exactly these `## ` headings, in this order, so Task 4's links resolve: `No alerts arrive`, `Site shows "Not reporting"`, `Telegram bot is silent`, `Portal returns 503`, `Container unhealthy or restarting`, `Pin finds no gauges`, `Pin page stuck pending`, `Alerts stopped after pause or stop`, `Admin password hash rejected`, `Tests cannot find tests directory`, `Tests cannot reach the database`.

- [ ] **Step 1: Write `telegram-bot.md`**

Steps: BotFather `/newbot`, copy the token; portal Settings → Notification Channels → paste the token → save; within 30 s the log shows `TelegramAdapter started polling` and the setting `telegram_bot_username` fills in (visible on Settings); set `public_base_url` (Settings → Monitoring) to the address users reach the portal at; send `/start` to the bot and expect the welcome with a map link; open the link, drop a pin, save; expect the confirmation message in the chat; `## Commands users can send` linking to `../reference/telegram-commands.md`; `## Legacy page subscriptions` (`/subscribe <public_token>` for admin-created pages). Close with `## If it went wrong`.

- [ ] **Step 2: Write `twilio-and-facebook.md`**

Two sections of steps taken from the current README's Twilio and Facebook sections, corrected: credentials are entered on the Settings page (not `.env`); webhook URLs use `<portal-host>` and must be HTTPS behind the proxy; Twilio status callbacks go to `/webhook/twilio/status`; both webhooks reject unsigned or unconfigured requests (link `../explanation/security-model.md`); `JOIN` / `STOP` keywords; where subscribers appear (Subscribers page, broadcast-only unless attached to a page on its editor). Close with `## If it went wrong`.

- [ ] **Step 3: Write `add-gauges-as-admin.md`**

Steps: Sites page → add by USGS number with parameter code, or search by name (USGS + NOAA combined results, paging); what the health badge means (`site_stale_hours`); Pages → `/pages/new` to create a landing page, copy the public and edit links; on the editor attach USGS sites and NOAA gauges (search by name or LID; a USGS number resolves to its NOAA LID); subscribe a recipient by channel and id; the sensitivity control; the Admin → Pages list and the active toggle; `## How this differs from pin pages` (origin `admin`, never retired, default sensitivity `all`). Close with `## If it went wrong`.

- [ ] **Step 4: Write `diagnose.md`**

Lead sentence, then one `## ` section per heading above. Each section is a table with columns `likely cause | check | fix`, three to six rows, every *check* a concrete command or portal page, every *fix* linking to the how-to or reference that covers it. Required rows: no alerts → page not `active` or `status` not active, subscriber not active, sensitivity too low for the alert kind, bot token missing, Telegram `send` failing in logs, site never crossed a threshold (check `site_conditions`); not reporting → USGS returns no interval data for the parameter (wrong `parameter_code`), no daily history since `historical_start_year`, USGS outage, `last_error` on the Sites page; bot silent → token unset or invalid (`TelegramAdapter` log lines), `public_base_url` empty (`NO_BASE_URL` reply), bot blocked by the user; portal 503 → no `ADMIN_PASSWORD_HASH`, malformed hash (the exact startup log text), `/healthz` 503 because a thread died; container unhealthy → thread death names in the `logger.critical` line, database unreachable (`DATABASE_URL`, `shared-db` network), root-owned logs volume; pin finds no gauges → off-network pin (more than 2 km from a flowline), NLDI outage (log `NLDI snap failed`), parameter check failed (log `USGS parameter check failed`) drops USGS candidates, `discovery_reach_km` too small; stuck pending → web-first page never bound (deep link not tapped), `telegram_bot_username` empty so the link is missing, chat already owns another page; stopped after pause or stop → `status`, `/resume`, a stopped page cannot be resumed (send `/start`); hash rejected → `$` eaten by compose or PowerShell, regenerate with the script; tests cannot find `tests/` → `tests/` listed in `.dockerignore`; tests cannot reach the database → running plain pytest outside Docker, shared-postgres down, wrong `TEST_DATABASE_URL`.

- [ ] **Step 5: Run the checker and commit**

Run: `python scripts/check_docs.py`
Expected: only `missing document docs/README.md` and `docs/tutorials/first-run.md` failures remain.

```bash
git add docs/howto
git commit -m "docs: product how-tos — telegram, twilio/facebook, admin gauges — and the diagnosis table"
```

---

### Task 6: Tutorial and index

**Files:**
- Create: `docs/tutorials/first-run.md`, `docs/README.md`

**Interfaces:**
- Consumes: every page from Tasks 2–5.
- Produces: `docs/README.md` with headings `## Start here`, `## Tutorials`, `## How-to guides`, `## Reference`, `## Explanation`, one bullet per document with a one-line description, plus `## This deployment` pointing at `howto/this-deployment.md`.

- [ ] **Step 1: Write `first-run.md`**

A single narrative (900–1400 words) for a generic Docker host and any reachable PostgreSQL, in numbered stages: clone; create the database and role (`psql` statements for `CREATE ROLE river`, `CREATE DATABASE rivermonitor OWNER river`); `.env`; admin password (link `../howto/secrets.md`); `docker compose up -d --build`; open the portal and log in; add one USGS site by number (use `03294500`, Ohio River at Louisville, as the example) and watch it get its first reading in the log; create the Telegram bot and set the token (link `../howto/telegram-bot.md`); set `public_base_url`; send `/start`, drop a pin near the example site, save, receive the confirmation; explain that the first real alert arrives when a threshold is crossed and how to force one for a test (temporarily set `high_percentile` low on Settings, wait one poll interval, restore it). End with "Where to go next" links.

- [ ] **Step 2: Write `docs/README.md`**

The index as specified in Interfaces. Each bullet: `- [Title](path) — one line.` Group the how-tos into "Run it", "Connect channels", "Manage gauges and pages", "When something is wrong".

- [ ] **Step 3: Run the checker and commit**

Run: `python scripts/check_docs.py`
Expected: `OK`, exit 0. If any link fails, fix the link, not the target.

```bash
git add docs/tutorials docs/README.md
git commit -m "docs: first-run tutorial and manual index"
```

---

### Task 7: README landing page, legacy cleanup, final verification

**Files:**
- Modify: `README.md` (rewrite), `CLAUDE.md` (one added line)
- Delete: `docs/docker-ssh-access.md`, `docs/docker-ssh-access.html`

- [ ] **Step 1: Rewrite `README.md`**

Keep the title and the first paragraph's substance (what the system does, USGS + NOAA, per-user alerts, Telegram/SMS/WhatsApp/Messenger). Then:

```markdown
## Quick start

1. `cp .env.example .env` and fill `DATABASE_URL`, `TEST_DATABASE_URL`, `FLASK_SECRET_KEY`.
2. `uv run --with werkzeug python set_admin_password.py` — sets the portal password in `.env`.
3. `docker compose up -d --build` (needs a reachable PostgreSQL; see the tutorial).
4. Open `http://localhost:5743`, log in, and paste your Telegram bot token on **Settings → Notification Channels**.
5. Set **public base URL** on **Settings → Monitoring**, then send `/start` to your bot.

## Documentation

| | |
|---|---|
| [Start here](docs/README.md) | Index of the whole manual |
| [First run](docs/tutorials/first-run.md) | Clean machine to first alert |
| [How-to guides](docs/README.md#how-to-guides) | Deploy, upgrade, back up, connect channels, diagnose |
| [Reference](docs/README.md#reference) | Every setting, variable, table, route, command, thread |
| [Explanation](docs/README.md#explanation) | How classification, routing, discovery and retirement work |

Developers: see [CLAUDE.md](CLAUDE.md).
```

Then a short `## Features` list (at most 8 bullets) and `## Resources` (the external USGS/NOAA/Twilio/Telegram links from the current README). Delete every other section; their content now lives in `docs/`.

- [ ] **Step 2: Add the CLAUDE.md pointer**

Insert as the line after the `# CLAUDE.md` heading:

```
Operator documentation lives in `docs/README.md`; keep it in sync when settings, routes, tables or commands change (run `python scripts/check_docs.py`).
```

- [ ] **Step 3: Remove the legacy SSH document**

```bash
git rm docs/docker-ssh-access.md docs/docker-ssh-access.html
```

Then grep `docs/`, `README.md` and `CLAUDE.md` for `docker-ssh-access` and fix any remaining reference to point at `docs/howto/this-deployment.md`.

- [ ] **Step 4: Final verification**

Run: `python scripts/check_docs.py` — expected `OK`.
Run: `grep -rn "hpz440" docs README.md` — expected no output (and the same for the SSH user name and the maintainer's GitHub handle).
Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web -q` — expected all pass (no code changed; this proves the image still builds with the new files present).

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: README landing page; fold the SSH guide into the deployment runbook"
```

---

## Self-review notes

- **Spec coverage:** §1 inventory → Tasks 2–7 (28 documents plus README and index); §2 content rules → Global Constraints and each task's page brief; §3 process → Task 1 checker, one task per folder, README last, checker as the gate.
- **Corrections to older docs baked in:** channel credentials are database settings, not environment variables (README and `.env.example` comments say otherwise); the existing SSH guide already uses example identifiers, so folding it is a restructure, not a scrub.
- **`.env.example` is not edited** by this plan because it has unrelated uncommitted changes in the maintainer's working tree; its misleading commented channel lines are called out in `reference/environment.md` and can be removed in a follow-up.
- **Anchor consistency:** the anchors in Task 4 match the headings defined in Task 5 (lower-case, hyphenated, quotes dropped: `Site shows "Not reporting"` → `#site-shows-not-reporting`).
