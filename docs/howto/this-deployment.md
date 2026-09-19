# This deployment

> **This runbook describes the maintainer's own setup.** Replace
> `<docker-host>`, `<docker-user>`, `<portal-host>`, `<db-host>`, and
> `<db-password>` with the values in your `.env` and `~/.ssh/config`.

## Topology

A Windows CLI machine drives everything over SSH — there is no `docker` daemon
on the CLI machine itself. The remote host runs two independent Compose
projects joined by one Docker network: `shared-postgres`'s PostgreSQL server
(reachable at the hostname `postgres`, never a published port) and this
project's `app` container, which publishes `5743` for the portal.

```
Windows CLI machine (no local daemon)
   |  docker context "riverhost" over SSH
   v
<docker-host>  ---------------------------------
  network: shared-db (external)
    [postgres]   <- owned by the shared-postgres project
        ^
        | DATABASE_URL (role: river)
    [app] -- publishes 5743 --> http://<portal-host>:5743
----------------------------------------------------------
```

## Docker context

Every command below runs on the Windows CLI machine unless noted.

1. Generate an SSH key for this machine, if you don't already have one.
   ```bash
   ssh-keygen -t ed25519 -C "<docker-user>@<docker-host>"
   ```
   Expected: a new key pair under `~/.ssh/`.

2. Copy the public key to `<docker-host>` (Windows has no `ssh-copy-id`).
   ```powershell
   type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh <docker-user>@<docker-host> "mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys"
   ```
   Expected: no output/error; a following plain `ssh <docker-user>@<docker-host>`
   opens a shell with no password prompt.

3. Create a Docker context that points at the daemon over that SSH login.
   ```bash
   docker context create riverhost --docker "host=ssh://<docker-user>@<docker-host>"
   ```
   Expected: `riverhost` printed as the created context name.

4. Switch to it.
   ```bash
   docker context use riverhost
   ```
   Expected: `docker context ls` shows `riverhost` marked with `*`.

5. Verify it is really talking to the remote daemon.
   ```bash
   docker info
   ```
   Expected: the server details in the output describe `<docker-host>`, not
   this machine.

6. Optional — add a `~/.ssh/config` alias so later commands can say
   `riverhost` instead of `<docker-user>@<docker-host>` (the file lives at
   `C:\Users\<you>\.ssh\config` on Windows):
   ```
   Host riverhost
       HostName <docker-host>
       User <docker-user>
       IdentityFile ~/.ssh/id_ed25519
   ```
   Expected: `ssh riverhost` connects without naming the user or host. Note:
   the `ControlMaster`/`ControlPath` connection-multiplexing directives are
   **not supported by Windows' built-in OpenSSH** — leave them out here; they
   work from Git Bash, WSL, Linux, and macOS if a command is ever driven from
   one of those instead.

> ⚠️ **Docker access is root-equivalent.** Anyone who can run `docker`
> against `<docker-host>` can take over the whole machine. Only grant this to
> people you trust with root.

For a web UI with per-user, per-container access control instead of shell
access, install [Portainer](https://www.portainer.io/) on `<docker-host>` —
it coexists with this context and needs no SSH key of its own.

## Shared PostgreSQL

1. Start the shared server once, from the **`shared-postgres`** project
   directory (a separate project — not this one).
   ```bash
   docker compose up -d
   ```
   Expected: that project's `docker compose ps` shows its postgres container
   running (`healthy` once its own healthcheck passes).

River Monitor connects to it as role `river` against databases `rivermonitor`
and `river_test` on the `shared-db` external network — it never runs its own
database container. The `river` password in this project's `.env` (below)
must equal `RIVER_DB_PASSWORD` in the shared-postgres project's `.env`, or
every connection will authenticate-fail.

## Environment file

1. Copy the template.
   ```bash
   cp .env.example .env
   ```
   Expected: a new `.env` file (gitignored) alongside `.env.example`.

2. Fill in the database URLs with the shared `river` password.
   ```
   DATABASE_URL=postgresql://river:<db-password>@postgres:5432/rivermonitor
   TEST_DATABASE_URL=postgresql://river:<db-password>@postgres:5432/river_test
   ```
   Expected: both lines present in `.env` with the real password in place of
   `<db-password>`.

3. Generate and set `FLASK_SECRET_KEY`.
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(24))"
   ```
   Expected: a random string printed; paste it as `FLASK_SECRET_KEY=<value>`
   in `.env`.

4. Set the admin password hash — see [`secrets.md`](secrets.md) for the
   generator and why it needs care. Expected: `ADMIN_PASSWORD_HASH` is a
   non-empty value in `.env` with every `$` written twice.

## Deploy

1. Build and start.
   ```bash
   docker compose up -d --build
   ```
   Expected: the image builds and the `app` container is created and started.

2. Wait for it to report healthy.
   ```bash
   docker compose ps
   ```
   Expected: the `app` service shows `healthy` within about 60 seconds (the
   healthcheck has a 30 s start period before it counts failures).

3. Check the startup log.
   ```bash
   docker compose logs --tail 30 app
   ```
   Expected: a line for every thread in [`../reference/threads.md`](../reference/threads.md)
   starting, and no traceback.

## Where things are

- **Logs** live in the named volume `app_logs`, not on this machine (the
  daemon is remote, so there is no host path to look at). Follow them with
  `docker compose logs -f app`, or read the file directly:
  ```bash
  docker run --rm -v my_river_level_app_logs:/logs alpine tail /logs/river_monitor.log
  ```
  Expected: the tail of `river_monitor.log`.
- **The portal** is at `http://<portal-host>:5743`, guarded by HTTP Basic
  auth.
- **Channel credentials and `public_base_url`** (Telegram token, Twilio,
  Facebook) are set on the portal's Settings page, not in `.env` — see
  [`../explanation/security-model.md`](../explanation/security-model.md).

## Constraints of this setup

- **No host bind mounts.** The daemon is remote, so a `-v ./local:/in-container`
  mount would resolve on `<docker-host>`, not this machine. `docker-compose.yml`
  uses the named volume `app_logs` instead, which works over the same SSH
  connection.
- **No buildx on the CLI machine**, so builds use the classic builder, which
  ignores `Dockerfile.test.dockerignore`. `tests/` therefore has to stay out
  of `.dockerignore` and ride along in every build context; the production
  `Dockerfile` strips it back out of the runtime image with `RUN rm -rf tests`.
- **One command at a time.** Chaining or piping `docker`/`docker compose`
  commands makes a remote-daemon failure harder to isolate — run each one
  separately, in order.

## If it went wrong

- Container never reports healthy — [`diagnose.md#container-unhealthy-or-restarting`](diagnose.md#container-unhealthy-or-restarting)
- Portal returns 503 — [`diagnose.md#portal-returns-503`](diagnose.md#portal-returns-503)
- `ADMIN_PASSWORD_HASH` rejected at startup — [`diagnose.md#admin-password-hash-rejected`](diagnose.md#admin-password-hash-rejected)
- No alerts arrive after the first deploy — [`diagnose.md#no-alerts-arrive`](diagnose.md#no-alerts-arrive)
