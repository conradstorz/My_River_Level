# Upgrade

1. Pull the latest code.
   ```bash
   git pull origin main
   ```
   Expected: local `main` fast-forwards; note the previous commit SHA
   (`git log -1 --format=%H HEAD@{1}` right after, or from your shell history)
   in case you need to roll back.

2. Rebuild and recreate the app container.
   ```bash
   docker compose up -d --build
   ```
   Expected: the image rebuilds and `app` is recreated; unrelated services
   (the `shared-postgres` project) are untouched.

3. Wait for it to report healthy.
   ```bash
   docker compose ps
   ```
   Expected: `app` shows `healthy` within about 60 seconds.

4. Read the startup log for errors.
   ```bash
   docker compose logs --tail 40 app
   ```
   Expected: `River Monitor starting` appears and no `ERROR` line follows it.

5. Confirm the schema migrated.
   ```bash
   docker compose exec app python -c "from db.models import get_conn; c=get_conn(); cur=c.cursor(); cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='user_pages' ORDER BY ordinal_position\"); print([r['column_name'] for r in cur.fetchall()])"
   ```
   Expected: a Python list of column names ending with `status` — the newest
   column `init_db`'s migrations add to `user_pages` (see
   [`../reference/database.md`](../reference/database.md)). Its presence
   confirms `init_db`'s additive migrations ran on this start.

## Rolling back

Migrations are additive only — every statement is `ADD COLUMN IF NOT EXISTS`
or `CREATE INDEX IF NOT EXISTS`, so an older image runs fine against the
newer schema; it simply doesn't read the newest columns.

1. Check out the previous commit.
   ```bash
   git checkout <previous-sha>
   ```
   Expected: the working tree matches the last known-good state.

2. Rebuild and recreate.
   ```bash
   docker compose up -d --build
   ```
   Expected: `app` comes back `healthy` running the older code.

## First upgrade from a pre-hardening deployment

The container runs as the non-root user `river` (uid 10001). Docker does
**not** re-chown a volume that already exists, so an `app_logs` volume created
by an older image is still root-owned and logging will fail with a
permission error until it's fixed once:

```bash
docker run --rm -v my_river_level_app_logs:/logs alpine chown -R 10001:10001 /logs
```

Expected: no output; the command exits 0.

You must also set `ADMIN_PASSWORD_HASH` in `.env` before the next
`docker compose up` — see [`secrets.md`](secrets.md) — or compose refuses to
start at all (`environment: ADMIN_PASSWORD_HASH: ${ADMIN_PASSWORD_HASH:?...}`
fails loudly rather than starting unprotected).

## If it went wrong

- Container never reports healthy — [`diagnose.md#container-unhealthy-or-restarting`](diagnose.md#container-unhealthy-or-restarting)
- Portal returns 503 — [`diagnose.md#portal-returns-503`](diagnose.md#portal-returns-503)
- `ADMIN_PASSWORD_HASH` rejected at startup — [`diagnose.md#admin-password-hash-rejected`](diagnose.md#admin-password-hash-rejected)
