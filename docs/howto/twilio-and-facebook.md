# Setting up Twilio (SMS/WhatsApp) and Facebook Messenger

Both channels' credentials are portal Settings, not `.env` — the
`TELEGRAM_BOT_TOKEN`, `TWILIO_*`, and `FACEBOOK_*` lines in `.env.example`
are template text the application never reads. Both inbound webhooks reject
unsigned or unconfigured requests; see
[`../explanation/security-model.md`](../explanation/security-model.md).

## Twilio (SMS / WhatsApp)

1. Create a [Twilio](https://www.twilio.com/) account and provision a phone
   number (or enable the WhatsApp sandbox/sender for that number).
   Expected: a Twilio phone number in E.164 form, e.g. `+15551234567`.

2. Open the portal Settings page and its Notification Channels tab.
   ```
   http://<portal-host>:5743/settings/channels
   ```
   Expected: a **Twilio (SMS / WhatsApp)** section.

3. Enter **Twilio Account SID**, **Twilio Auth Token**, and the **Twilio SMS
   Number** and/or **Twilio WhatsApp Number**, then click **Save
   Notification Channels**.
   Expected: "Settings saved."; the auth token also verifies the two
   webhooks below, so nothing inbound works until this step is done.

4. In the Twilio console, set the number's inbound webhook to this project's
   webhook, over HTTPS behind the reverse proxy (see
   [`reverse-proxy.md`](reverse-proxy.md)):
   ```
   https://<portal-host>/webhook/twilio
   ```
   Expected: Twilio's console accepts the URL (it will reject a plain
   `http://` URL for a number requiring HTTPS).

5. Also set the number's **Status Callback URL** — a separate field from step
   4 — to:
   ```
   https://<portal-host>/webhook/twilio/status
   ```
   Expected: Twilio starts POSTing delivery status (`delivered`,
   `undelivered`, `failed`) here after every outbound send.

6. From a phone (or the WhatsApp sandbox), text `JOIN` to the Twilio number.
   Expected: no reply text is sent back — the webhook returns an empty TwiML
   response — but the sender now appears as an active row on the portal
   Subscribers page.
   ```
   http://<portal-host>:5743/subscribers
   ```

7. Text `STOP` to unsubscribe.
   Expected: the same row's `active` flag clears; `UNSUBSCRIBE` works
   identically.

Subscribers who joined this way are **broadcast-only** — added to the
global `subscribers` table, not to any landing page. To have them hear about
a specific gauge, subscribe that same phone number on the page's editor
(`/edit/<edit_token>`, **Subscribe to Alerts** card) instead — see
[`add-gauges-as-admin.md`](add-gauges-as-admin.md).

## Facebook Messenger

1. Create a Facebook App with the Messenger product enabled, and generate a
   Page Access Token for the page you want alerts to come from.
   Expected: a long-lived page access token string.

2. Open the portal Settings page and its Notification Channels tab.
   ```
   http://<portal-host>:5743/settings/channels
   ```
   Expected: a **Facebook Messenger** section with **Facebook Page Token**
   and **Facebook Verify Token** fields.

3. Enter the **Facebook Page Token**, and a **Facebook Verify Token** of your
   own choosing (any string — Facebook echoes it back during webhook
   verification), then save.
   Expected: "Settings saved."

4. Set the app's **App Secret** as the `facebook_app_secret` setting. This
   one has no portal form field — set it from the CLI:
   ```bash
   docker compose exec app python -c "from db.models import set_setting; set_setting('facebook_app_secret', '<app-secret>')"
   ```
   Expected: no output. Every inbound Facebook webhook is HMAC-verified
   against this value; leaving it unset makes every POST return 403.

5. In the Facebook App dashboard, set the webhook URL to this project's
   webhook, over HTTPS, and the verify token to the same value from step 3:
   ```
   https://<portal-host>/webhook/facebook
   ```
   Expected: Facebook's verification GET request succeeds (it echoes
   `hub.challenge` back) and the subscription is created.

6. Message `JOIN` to the Facebook page.
   Expected: the sender's PSID appears as an active row on the portal
   Subscribers page, the same as the Twilio flow above. As with Twilio,
   this is broadcast-only until also subscribed on a specific page's editor.

## If it went wrong

- No alerts arrive — [`diagnose.md#no-alerts-arrive`](diagnose.md#no-alerts-arrive)
- Portal returns 503 — [`diagnose.md#portal-returns-503`](diagnose.md#portal-returns-503)
