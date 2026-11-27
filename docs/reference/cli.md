# CLI

Commands an operator runs, all from the project directory.

## Run

| command | purpose | notes |
|---|---|---|
| `docker compose down` | Stop and remove the app container. | The named volume `app_logs` and the external `shared-db`, `harbor` and `public-edge` networks are left in place. |
| `docker compose exec app python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:5743/healthz').read())"` | Check `/healthz` from inside the running container. | Useful when the host can't reach the published port; returns `{"status": "ok"/"degraded", "threads": {...}}`. |
| `docker compose logs -f app` | Follow the app's logs. | Same content as `logs/river_monitor.log` in the container (rotating, 5 MB, 3 backups). |
| `docker compose ps` | Show container status and health. | Health comes from the `healthcheck` in `docker-compose.yml`, which itself polls `/healthz`. |
| `docker compose restart app` | Restart the app container without rebuilding. | Picks up a changed `.env` value, or recovers a container whose supervisor exited non-zero. |
| `docker compose up -d --build` | Build the image and start (or update) the app in the background. | Requires the shared-postgres server already running (`docker compose up -d` in that project) and a completed `.env` — see [`environment.md`](environment.md). |
| `python scripts/smoke_public_edge.py [--base-url <url>] [--view-token <public_token>]` | Smoke-check the public edge from the CLI machine. | Default base URL `https://river.ohr3023.org`. Expects 200 from `/healthz`, `/pin` and `/view/<public_token>` (skipped without `--view-token`), 404 from `/`, `/settings`, `/webhook/twilio`, `/pages/new` and `/admin/pages`; prints one `PASS`/`FAIL` line per check (the view token shown only as its first three characters) and exits 1 on any mismatch. Standard library only. Each run's `/pin` request creates one pending page and counts against that route's rate limit. See [`../howto/this-deployment.md`](../howto/this-deployment.md#public-edge). |

## Deploy / upgrade

| command | purpose | notes |
|---|---|---|
| `docker compose up -d --build` | Rebuild the image from the checked-out source and recreate the container (deploy and upgrade). | `docker-compose.yml` builds from local source (`build: .`, no `image:`) — there is no registry image to pull. Full procedure: [`../howto/upgrade.md`](../howto/upgrade.md). |
| `bash scripts/go_live_wizard.sh [--help]` | Interactive go-live wizard for the public edge (Git Bash, CLI machine). | Must run from the main checkout on an up-to-date `main`. Creates the tunnel repo's private remote and the `public-edge` network if missing (and pushes the tunnel repo again if an earlier push failed or `origin` is behind), captures `TUNNEL_TOKEN` into the tunnel `.env` from a pasted token or connector command, sets `TRUSTED_PROXY_COUNT=1`, rebuilds River, writes `telegram_bot_token` and `public_base_url` to Settings over `docker exec` stdin, starts the tunnel, then runs the smoke check. If no page exists yet the `/view` check is skipped and the closing summary says to re-run the smoke script with `--view-token` after the first pin save. Re-runnable; `--help` only prints the stages. See [`../howto/this-deployment.md`](../howto/this-deployment.md#public-edge). |

## Tests

| command | purpose | notes |
|---|---|---|
| `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest -q` | Run the full suite inside Docker against the shared PostgreSQL server. | `river_test` is auto-created if missing; `--build` picks up source and test changes, since the test image bakes them in. |
| `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest -q tests/monitor/test_polling.py` | Run one test file (or any pytest path/`-k` expression) inside Docker. | Append the path after `test pytest -q` exactly as you would to a local `pytest` invocation. |
| `pytest` | Run the suite directly on the host. | Needs a reachable PostgreSQL and `TEST_DATABASE_URL` — see [`environment.md`](environment.md). |
| `TEST_DB_SUFFIX=_x docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest -q` | Run the suite against a distinct test database. | Lets parallel test runs avoid colliding on `river_test`; `TEST_DB_SUFFIX` is appended to `TEST_DATABASE_URL` by the overlay. |

## Secrets

| command | purpose | notes |
|---|---|---|
| `docker inspect my_river_level-app-1 --format '{{range .Config.Env}}{{println .}}{{end}}' \| grep ADMIN_PASSWORD_HASH` | Confirm the hash survived into the running container with its escaping intact. | Should show a hash with two single `$` separators; none means compose ate them — see [`../howto/secrets.md`](../howto/secrets.md). |
| `uv run --with werkzeug python set_admin_password.py` | Prompt twice (without echo) for a new portal password, then write the escaped hash to `ADMIN_PASSWORD_HASH` in `.env`. | Doubles every `$` in the werkzeug hash to `$$` itself, so it survives docker compose interpolation without hand-escaping; prints `Wrote <path> (<n> separators escaped).` and reminds you to run `docker compose up -d --force-recreate app`. |

## Database

| command | purpose | notes |
|---|---|---|
| `docker compose exec app python -c "from db.models import get_setting; print(get_setting('poll_interval_minutes'))"` | Read one setting's current value. | Substitute any key from [`settings.md`](settings.md) for `poll_interval_minutes`. |
| `docker exec <postgres-container> pg_dump -U river -Fc rivermonitor > rivermonitor-$(date +%F).dump` | Dump the production database (custom format) through the shared-postgres container. | The server publishes no host port and the daemon is remote, so `pg_dump` runs inside its container; `<postgres-container>` is the name shown by `docker ps` in the shared-postgres project. Full procedure: [`../howto/backup-restore.md`](../howto/backup-restore.md). Substitute `river_test` for the test database. |
| `docker exec -i <postgres-container> pg_restore -U river -d rivermonitor --clean --if-exists < rivermonitor-<date>.dump` | Restore a custom-format dump into the database. | Assumes the database exists; `init_db` re-applies the schema on the app's next start. |
| `docker compose exec app python -c "from db.models import set_setting; set_setting('<key>', '<value>')"` | Change a settings-table value that has no portal field (the `rate_change_*` keys, `site_stale_hours`, `forecast_poll_hours`). | The reading thread picks it up on its next cycle; see [`settings.md`](settings.md) for the full key list. |
| `docker compose exec app python -c "from db.models import get_db; c=get_db(); cur=c.cursor(); cur.execute('select id, page_name, owner_chat_id, status, edit_token from user_pages order by id'); [print(dict(r)) for r in cur.fetchall()]"` | List every page with its edit token, to open a user's editor on their behalf or answer who owns what. | See [`../howto/add-gauges-as-admin.md`](../howto/add-gauges-as-admin.md) for closing or inspecting a page. |

Verified against commit 39c5272
