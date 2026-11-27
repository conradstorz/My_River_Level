# This deployment

> **This runbook describes the maintainer's own setup.** Replace
> `<docker-host>`, `<docker-user>`, `<portal-host>`, `<db-host>`, and
> `<db-password>` with the values in your `.env` and `~/.ssh/config`.

## Topology

A Windows CLI machine drives everything over SSH — there is no `docker` daemon
on the CLI machine itself. The remote host runs independent Compose projects
joined by external Docker networks: `shared-postgres`'s PostgreSQL server
(reachable at the hostname `postgres`, never a published port), Traefik on the
`harbor` network (serving the whole app, portal included, at `<portal-host>`
on the tailnet only), the `cloudflare-tunnel` project's `cloudflared` on the
`public-edge` network (serving only the public pages at
`https://river.ohr3023.org`), and this project's `app` container, which joins
all three.

```
Windows CLI machine (no local daemon)
   |  docker context "riverhost" over SSH
   v
<docker-host>  ------------------------------------------------------
  network: shared-db (external)
    [postgres]   <- owned by the shared-postgres project
        ^
        | DATABASE_URL (role: river)
    [app :5743] <-- harbor ------ [traefik]     <- https://<portal-host> (tailnet)
        ^
        | public-edge, alias river-app (bypasses Traefik)
    [cloudflared] <- owned by the cloudflare-tunnel project
        ^
        | outbound-only tunnel; Cloudflare admits the public paths only
     https://river.ohr3023.org (internet)
----------------------------------------------------------------------
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

`docker-compose.yml` joins three external networks — `shared-db`, `harbor`
and `public-edge` — and compose refuses to create the `app` container while
any of them is missing. `public-edge` is owned by no project: create it once
(step 1 of [Public edge](#public-edge)) before the first deploy that includes
it. For the first public go-live, `scripts/go_live_wizard.sh` does this and
the deploy for you — see [Public edge](#public-edge).

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
- **The portal** is at `https://<portal-host>` through Traefik on the
  tailnet, guarded by HTTP Basic auth. It is never reachable through the
  public hostname.
- **The public pages** (map, page view and edit, `/healthz`) are at
  `https://river.ohr3023.org` through the Cloudflare tunnel — see
  [Public edge](#public-edge).
- **Channel credentials and `public_base_url`** (Telegram token, Twilio,
  Facebook) are set on the portal's Settings page, not in `.env` — see
  [`../explanation/security-model.md`](../explanation/security-model.md).

## Public edge

The public hostname `https://river.ohr3023.org` is served by a remote-managed
Cloudflare tunnel run from the separate `cloudflare-tunnel` project (a sibling
of `shared-postgres`). `cloudflared` makes only outbound connections, so
`<docker-host>` publishes no port to the internet. It joins the external
`public-edge` Docker network and forwards straight to the app at
`http://river-app:5743` — the network alias `docker-compose.yml` gives the
`app` service on `public-edge`. It bypasses Traefik; the tailnet path through
Traefik at `<portal-host>` is unchanged.

**Going live the first time? Run the wizard instead of the steps below.**
From the main River checkout, on an up-to-date `main`, in Git Bash:

```bash
bash scripts/go_live_wizard.sh
```

It walks through every human-only step in a safe order: preflight (River on
an up-to-date `main`), the tunnel repo's private GitHub remote, the
`public-edge` network, the Cloudflare tunnel token and route,
`TRUSTED_PROXY_COUNT=1`, the River rebuild, the Telegram bot token and
`public_base_url` (written to Settings over `docker exec` stdin, never
echoed), starting the tunnel, the smoke check below, and a `/start` to the
bot. It is safe to re-run: finished steps are skipped, and a tunnel-repo push
that failed is retried. At first go-live no page exists yet, so the smoke
check's `/view` test is skipped and the closing summary says so; after your
first pin save, run step 4 below with that page's `public_token`. `--help`
lists the stages without doing anything.

The tunnel's ingress rules live in the Cloudflare dashboard, not in either
repo; the `cloudflare-tunnel` README records them. One rule for
`river.ohr3023.org` admits only `/pin`, `/pin/...`, `/view/...`, `/edit/...`,
`/static/...` and `/healthz`, with the path regex

```
^/(pin(/.*)?|view/.+|edit/.+|static/.+|healthz)$
```

and a catch-all returns 404. The admin portal, page creation (`/pages/new`)
and the channel webhooks (`/webhook/...`) therefore stay tailnet-only.

1. Create the shared network once on `<docker-host>`, before starting either
   project.
   ```bash
   docker network create public-edge
   ```
   Expected: a network ID printed; `docker network ls` lists `public-edge`.

2. Set `TRUSTED_PROXY_COUNT=1` in this project's `.env`, then recreate the app.
   ```bash
   docker compose up -d --build
   ```
   Expected: `docker compose ps` shows `app` healthy. Exactly one proxy hop
   sits in front of the app on either path (Traefik on the tailnet,
   `cloudflared` on the public edge), so the pin-flow rate limits key on the
   real visitor — see [`../reference/environment.md`](../reference/environment.md).

3. Start the tunnel from the `cloudflare-tunnel` project directory, after
   filling `TUNNEL_TOKEN` in its `.env`.
   ```bash
   docker compose up -d
   ```
   Expected: the tunnel shows **Healthy** in the Cloudflare dashboard.

4. From the CLI machine, smoke-check the public edge.
   ```bash
   python scripts/smoke_public_edge.py --view-token <public_token>
   ```
   Expected: seven `PASS` lines (200 from `/healthz`, `/pin` and
   `/view/<public_token>`; 404 from `/`, `/settings`, `/webhook/twilio` and
   `/pages/new`)
   and exit status 0; the token is printed only as its first three
   characters. Take `<public_token>` from any live page. Each run's
   `/pin` request creates one pending page, swept after 7 days. Against
   `--base-url https://<portal-host>` the 404 checks fail by design (the
   tailnet serves everything), but `/healthz` should show 200.

### Cloudflare edge settings

Set these once in the Cloudflare dashboard for `ohr3023.org`; the app does
not set them itself (HSTS in particular belongs at the edge, which is the
only hop that speaks HTTPS):

- [ ] **Always Use HTTPS** on (SSL/TLS → Edge Certificates), so a plain
      `http://` link is redirected before it reaches the tunnel.
- [ ] **HSTS** enabled (SSL/TLS → Edge Certificates → HTTP Strict Transport
      Security), so browsers never retry the site over plain HTTP.
- [ ] **Rate-limiting rule** (Security → WAF → Rate limiting rules) on
      `river.ohr3023.org` requests whose path starts with `/pin` or `/edit/`,
      blocking or challenging above a per-IP rate. This is the brake that
      holds across app restarts; the app's own limits are in-process.

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

## Data growth and logs

Logs rotate on their own: `main.py` configures a `RotatingFileHandler` at
5 MB with 3 backups, all inside the `app_logs` volume, so log growth is
bounded without any operator action.

The database is not. `site_conditions`, `noaa_observations`,
`gauge_forecasts`, and `notifications` each grow one row per poll (or per
delivery attempt) forever — there is no built-in retention or pruning. Back
up before deleting anything — see [`backup-restore.md`](backup-restore.md) —
then prune, for example, `site_conditions` rows older than a year:

```bash
docker exec <postgres-container> psql -U river -d rivermonitor -c "DELETE FROM site_conditions WHERE checked_at::timestamptz < NOW() - INTERVAL '1 year'"
```

Expected: `DELETE <n>` printed, naming how many rows were removed. The other
three tables can be pruned the same way, substituting the table name and its
own timestamp column (`observed_at` for `noaa_observations`, `issued_at` for
`gauge_forecasts`, `sent_at` for `notifications`).

## If it went wrong

- Container never reports healthy — [`diagnose.md#container-unhealthy-or-restarting`](diagnose.md#container-unhealthy-or-restarting)
- Portal returns 503 — [`diagnose.md#portal-returns-503`](diagnose.md#portal-returns-503)
- `ADMIN_PASSWORD_HASH` rejected at startup — [`diagnose.md#admin-password-hash-rejected`](diagnose.md#admin-password-hash-rejected)
- No alerts arrive after the first deploy — [`diagnose.md#no-alerts-arrive`](diagnose.md#no-alerts-arrive)
