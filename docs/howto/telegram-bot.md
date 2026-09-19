# Setting up the Telegram bot

1. Create the bot with BotFather. In Telegram, message
   [@BotFather](https://t.me/BotFather), send `/newbot`, and follow its
   prompts for a display name and a username ending in `bot`.
   Expected: BotFather replies with a token that looks like
   `123456789:AAExampleTokenTextHere`.

2. Open the portal Settings page and its Notification Channels tab.
   ```
   http://<portal-host>:5743/settings/channels
   ```
   Expected: a **Telegram** section with a **Telegram Bot Token** field.

3. Paste the token into **Telegram Bot Token** and click **Save Notification
   Channels**.
   Expected: the page redirects back to itself with a "Settings saved."
   banner.

4. Confirm the bot connected.
   ```bash
   docker compose logs --tail 30 app
   ```
   Expected: `TelegramAdapter started polling` within about 30 seconds
   (`TelegramAdapter.TOKEN_POLL_SECONDS`) of saving the token. There is no
   portal field showing the bot's username — confirm it was recorded instead
   with:
   ```bash
   docker compose exec app python -c "from db.models import get_setting; print(get_setting('telegram_bot_username'))"
   ```
   Expected: the bot's `@username`, printed with no leading `@`. An empty
   result with `Could not read the bot's username` in the logs means the
   token was accepted by the supervisor but the `getMe` call failed.

5. Set the address subscribers reach the portal at, on **Settings →
   Monitoring**.
   ```
   http://<portal-host>:5743/settings/monitoring
   ```
   Set **Public base URL** to `https://<portal-host>` (or the plain
   `http://<portal-host>:5743` form for a deployment with no reverse proxy)
   and save. Expected: "Settings saved."; this is the base every map, editor,
   and `/start` deep link is built from — see
   [`../reference/settings.md`](../reference/settings.md).

6. Send `/start` to the bot from Telegram.
   Expected: a welcome reply ending in a map link, e.g. `Welcome! Pick the
   spot on the river you care about and I'll watch the gauges
   there:\nhttps://<portal-host>/pin/<edit_token>`.

7. Open that link, drop a pin on the map, and save it.
   Expected: the map's confirm screen lists the proposed USGS/NOAA gauges;
   saving closes it.

8. Check the chat for the save confirmation.
   Expected: a message of the form `✓ You're set up for <river>. You'll hear
   about:\n• <gauge name>\n...\n\nSend /settings any time to change gauges or
   sensitivity.` — see [`../reference/alerts.md`](../reference/alerts.md) for
   the exact `direct` message format.

## Commands users can send

See [`../reference/telegram-commands.md`](../reference/telegram-commands.md)
for every command and callback the bot understands, argument by argument,
with its exact reply text.

## Legacy page subscriptions

A page created by an admin on the portal (`/pages/new`) has no pin and no
owning chat, so it is reached differently: a subscriber sends
`/subscribe <public_token>`, where `<public_token>` is the code in that
page's public `/view/<public_token>` URL. `/subscribe` with no code instead
opts the chat into the global broadcast list, unrelated to any page. This is
the only way to attach a Telegram chat to an admin-created page — pin pages
never need it, since dropping a pin both creates the page and binds the chat
in one step.

## If it went wrong

- Telegram bot is silent — [`diagnose.md#telegram-bot-is-silent`](diagnose.md#telegram-bot-is-silent)
- Pin finds no gauges — [`diagnose.md#pin-finds-no-gauges`](diagnose.md#pin-finds-no-gauges)
- Pin page stuck pending — [`diagnose.md#pin-page-stuck-pending`](diagnose.md#pin-page-stuck-pending)
- Alerts stopped after pause or stop — [`diagnose.md#alerts-stopped-after-pause-or-stop`](diagnose.md#alerts-stopped-after-pause-or-stop)
- No alerts arrive — [`diagnose.md#no-alerts-arrive`](diagnose.md#no-alerts-arrive)
