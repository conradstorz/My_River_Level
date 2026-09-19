# Rotating secrets

## `DATABASE_URL` / `TEST_DATABASE_URL`

1. Rotate the `river` role's password in the **shared-postgres** project (a
   separate project) and update `RIVER_DB_PASSWORD` in its `.env`.
   Expected: that project's own instructions confirm the role's password
   changed.

2. Update `DATABASE_URL` and `TEST_DATABASE_URL` in this project's `.env`
   with the new `<db-password>`.
   Expected: both lines in `.env` carry the same new password.

3. Recreate the app container to pick it up.
   ```bash
   docker compose up -d --force-recreate app
   ```
   Expected: `docker compose ps` shows `app` healthy again. A password that
   didn't actually match would instead show it exiting immediately, with
   `docker compose logs app` reporting a `psycopg2.OperationalError`
   authentication failure.

## `FLASK_SECRET_KEY`

1. Generate a new key.
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(24))"
   ```
   Expected: a random string printed.

2. Set `FLASK_SECRET_KEY=<value>` in `.env` to it.
   Expected: the old value is gone from `.env`.

3. Recreate the app container.
   ```bash
   docker compose up -d --force-recreate app
   ```
   Expected: the app restarts; every existing portal session is invalidated,
   so every admin has to log in again.

## `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH`

1. Run the generator instead of hand-crafting the value.
   ```bash
   uv run --with werkzeug python set_admin_password.py
   ```
   Expected: it prompts for the new password twice without echoing it, then
   prints `Wrote <path to .env> (<n> separators escaped).` A werkzeug hash is
   `method$salt$hash`, and docker compose interpolates `$name` in `.env`, so
   every `$` has to be written as `$$` or the salt is silently eaten; the
   script does that escaping itself specifically so it doesn't have to be
   done in a shell one-liner, because in PowerShell a hand-typed `$$` is the
   "last token of the previous command" automatic variable and expands to
   nothing, which would delete the separators instead of doubling them.

2. Recreate the app container.
   ```bash
   docker compose up -d --force-recreate app
   ```
   Expected: the app restarts using the new credentials.

3. Verify the hash survived interpolation with its escaping intact.
   ```bash
   docker inspect my_river_level-app-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ADMIN_PASSWORD_HASH
   ```
   Expected: one line, `ADMIN_PASSWORD_HASH=method$salt$hash`, with exactly
   two single `$` separators — not `$$`, and not zero.

4. If step 3 shows zero separators (or fewer than expected), `app`'s startup
   log carries the diagnostic:
   ```bash
   docker compose logs app
   ```
   Expected, when malformed: a line reading
   `ADMIN_PASSWORD_HASH is not a valid werkzeug hash`, and every admin route
   returning 503 until it's fixed and step 2 is repeated.

## Channel tokens (Telegram, Twilio, Facebook app secret)

These are **not** environment variables — the commented `TELEGRAM_BOT_TOKEN`,
`TWILIO_*`, and `FACEBOOK_*` lines in `.env.example` are template text the
application never reads. Rotate them on the portal's Settings page
(`/settings`) instead; see [`../reference/settings.md`](../reference/settings.md)
for the full key list. A new Telegram token takes effect within about 30
seconds; Twilio and Facebook changes take effect on the next request.

## Never commit

- `.env`
- any `*.dump` backup file — it contains the whole `rivermonitor` database,
  channel-token-free but including subscriber contact details
- the output of `set_admin_password.py` — it is written straight into `.env`,
  never printed anywhere worth copying into a chat or ticket

## If it went wrong

- Portal returns 503 — [`diagnose.md#portal-returns-503`](diagnose.md#portal-returns-503)
- `ADMIN_PASSWORD_HASH` rejected at startup — [`diagnose.md#admin-password-hash-rejected`](diagnose.md#admin-password-hash-rejected)
- Telegram bot is silent after rotating its token — [`diagnose.md#telegram-bot-is-silent`](diagnose.md#telegram-bot-is-silent)
