# Backup and restore

Everything River Monitor needs to keep running lives in the `rivermonitor`
database — see [`../reference/database.md`](../reference/database.md) for
every table. A `pg_dump` of it captures sites, settings, subscribers, landing
pages, and history. It does **not** capture the application logs (rotating
files inside the `app_logs` volume) or `.env` (`DATABASE_URL`,
`TEST_DATABASE_URL`, `FLASK_SECRET_KEY`, `ADMIN_USERNAME`,
`ADMIN_PASSWORD_HASH`, `TRUSTED_PROXY_COUNT`) — back those up separately if
you need them. Channel credentials (Telegram token, Twilio, Facebook) are
`settings` rows, not `.env` values, so the dump *does* capture them — treat
dump files as secret for that reason, not just for the subscriber contact
details they also hold.

1. Find the shared-postgres project's container name.
   ```bash
   docker ps --filter "label=com.docker.compose.project=shared-postgres" --format "{{.Names}}"
   ```
   Expected: one container name printed, e.g. `shared-postgres-postgres-1`;
   use it in place of `<postgres-container>` below.

2. Dump the production database in the custom (restorable, compressed)
   format.
   ```bash
   docker exec <postgres-container> pg_dump -U river -Fc rivermonitor > rivermonitor-$(date +%F).dump
   ```
   Expected: a `rivermonitor-<date>.dump` file appears in the current
   directory on this machine, non-zero size. `docker exec` streams the dump
   back over the same connection the context uses, so the redirect writes
   locally even though `<postgres-container>` runs on `<docker-host>`.

3. Verify the dump is readable, without needing PostgreSQL client tools
   installed on this machine.
   ```bash
   docker exec -i <postgres-container> pg_restore --list < rivermonitor-$(date +%F).dump
   ```
   Expected: a table-of-contents listing every table (`sites`, `settings`,
   `user_pages`, ...), with no error.

4. Restore into a database — the same `rivermonitor` name to overwrite it, or
   a scratch database to test the restore without touching production.
   ```bash
   docker exec -i <postgres-container> pg_restore -U river -d rivermonitor --clean --if-exists < rivermonitor-$(date +%F).dump
   ```
   Expected: `pg_restore` prints its progress and exits 0. `NOTICE`s about
   objects that didn't exist to drop are normal on a fresh database;
   `--clean --if-exists` is what keeps those from being fatal.

5. What to expect afterward: once `app` is running against the restored
   database, `PollingThread` and `NoaaPollingThread` resume on their next
   cycle with no special action, and no alert fires for conditions that were
   already current before the backup — the restored `site_conditions` history
   is what the pollers diff the next reading against, so nothing looks like a
   fresh transition.

## Schedule

A daily cron job on `<docker-host>` (not through the SSH context — this runs
directly where the postgres container lives), keeping 14 days of dumps:

```
0 3 * * * docker exec <postgres-container> pg_dump -U river -Fc rivermonitor > /path/to/backups/rivermonitor-$(date +\%F).dump && find /path/to/backups -name 'rivermonitor-*.dump' -mtime +14 -delete
```

Adjust `/path/to/backups` to wherever backups are retained on that host.

## If it went wrong

- Container never reports healthy after a restore — [`diagnose.md#container-unhealthy-or-restarting`](diagnose.md#container-unhealthy-or-restarting)
- A site shows "Not reporting" after a restore — [`diagnose.md#site-shows-not-reporting`](diagnose.md#site-shows-not-reporting)
- No alerts arrive after a restore — [`diagnose.md#no-alerts-arrive`](diagnose.md#no-alerts-arrive)
