# Reverse proxy

Why: the portal accepts HTTP Basic credentials and shows subscriber data, so
it should never be reached over plain HTTP directly on `<docker-host>:5743`;
a reverse proxy terminates TLS, gives it one stable hostname
(`<portal-host>`), and is what the rest of this page configures River
Monitor to trust.

1. Add a server block on whatever host terminates TLS for `<portal-host>`
   (it need not be `<docker-host>` itself).
   ```nginx
   server {
       listen 443 ssl;
       server_name <portal-host>;

       ssl_certificate     /etc/letsencrypt/live/<portal-host>/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/<portal-host>/privkey.pem;

       location / {
           proxy_pass http://<docker-host>:5743;
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```
   Expected: `nginx -t` validates the config with no errors.

2. Reload nginx.
   ```bash
   sudo systemctl reload nginx
   ```
   Expected: `curl -I https://<portal-host>/healthz` returns `200`.

3. Tell River Monitor to trust exactly one proxy hop, in this project's
   `.env`:
   ```
   TRUSTED_PROXY_COUNT=1
   ```
   Expected: the line is present (`docker-compose.yml` forwards it from
   `.env`, defaulting to `0` — no proxy trusted — when absent).

4. Recreate the app container.
   ```bash
   docker compose up -d --force-recreate app
   ```
   Expected: `docker compose ps` shows `app` healthy.

5. Verify the proxy hop is trusted — send a request through the proxy as one
   simulated client.
   ```bash
   curl -I -H "X-Forwarded-For: 203.0.113.10" https://<portal-host>/pin
   ```
   Expected: `HTTP/1.1 302 FOUND` — a pending page was created and counted
   against `203.0.113.10`'s bucket on the `/pin` rate limit (10 per IP per
   day; see [`../reference/http-routes.md`](../reference/http-routes.md)).

6. Repeat as a different simulated client.
   ```bash
   curl -I -H "X-Forwarded-For: 203.0.113.11" https://<portal-host>/pin
   ```
   Expected: also `302 FOUND`, independent of step 5 — with
   `TRUSTED_PROXY_COUNT=1`, Flask's `ProxyFix` reads the real client address
   from `X-Forwarded-For` instead of attributing every request behind the
   proxy to the proxy's own address, so the two headers land in separate rate
   buckets. (Confirm the separation directly by repeating step 5's header 10
   more times — request 11 gets `429`, while step 6's address is unaffected.)

7. On the portal, open **Settings → Monitoring** and set `public_base_url`
   to `https://<portal-host>`, then save.

   Expected: the Telegram `/start` reply and the pin map now link to
   `https://<portal-host>/...`.

8. In the Twilio console (not this portal), set the messaging webhook to
   `https://<portal-host>/webhook/twilio` and the status callback to
   `https://<portal-host>/webhook/twilio/status`.

   Expected: an inbound test message reaches the portal log as a signed
   request (see [Twilio and Facebook](twilio-and-facebook.md)).

9. In the Facebook app dashboard (not this portal), set the Messenger
   webhook to `https://<portal-host>/webhook/facebook` and complete the
   verify-token challenge.

   Expected: the dashboard reports the webhook as verified.

## Health checks from the proxy

Point the proxy's own health check at `GET /healthz` — it needs no
credentials and no token (see
[`../explanation/security-model.md`](../explanation/security-model.md)) and
returns 503 if any worker thread has died, so a proxy-level check catches a
"serving pages but no longer monitoring" container the same way the Docker
healthcheck does.

## If it went wrong

- Portal returns 503 — [`diagnose.md#portal-returns-503`](diagnose.md#portal-returns-503)
- Container never reports healthy after the recreate — [`diagnose.md#container-unhealthy-or-restarting`](diagnose.md#container-unhealthy-or-restarting)
