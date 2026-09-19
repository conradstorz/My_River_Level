# Telegram commands

Commands the bot understands; a chat owns at most one live pin page.

| command or callback | argument | effect | reply |
|---|---|---|---|
| `/mypages` | none | List the pages this chat actively receives alerts for. | `You get alerts for:\n• <page name>\n...`, or, when subscribed to none: `You aren't subscribed to any river pages yet. Send /subscribe <page code> to add one.` |
| `/pause` | none | Set this chat's page `status` to `paused`, suppressing its alerts. | `⏸ Alerts paused. Send /resume when you want them back.`, or `NO_PAGE` if the chat has no page. |
| `/resume` | none | Set this chat's page `status` back to `active`. | `▶️ Alerts resumed.`, or `NO_PAGE`. |
| `/sensitivity` | none | Show the current sensitivity dial with buttons for each level (`sens:<level>` callbacks). | `How much do you want to hear? Now: <current level>`, or `NO_PAGE`. |
| `/settings` | none | Link to the page editor (`/edit/<edit_token>`). | `Manage your gauges and sensitivity here:\n<edit link>` — `NO_BASE_URL` in place of the link when `public_base_url` is unset, or `NO_PAGE` if the chat has no page. |
| `/sources` | none | List the page's linked USGS/NOAA gauges, each with a Remove button (`rm:usgs:<site_id>` / `rm:noaa:<gauge_id>` callbacks). | `You're watching:\n• <name> (USGS <site_number>)\n...`, or `No gauges yet. Send /start to pick a spot on the map.` if none, or `NO_PAGE`. |
| `/start` | none, or `<edit_token>` | No argument: return (or create) the chat's pin page, record a pending registration, and send its map link. With `<edit_token>`: bind that web-first page to this chat as owner; any other page the chat still had open is stopped, so the chat keeps exactly one live page. | No argument, pending: `Welcome! Pick the spot on the river you care about and I'll watch the gauges there:\n<link>`. No argument, active/paused: `You're already set up for <river>. Change it here:\n<link>\nOther commands: /settings /sensitivity /sources /pause /resume /stop` (paused adds a line about `/resume`). With `<edit_token>`: `TOKEN_NOT_FOUND` if the token is unknown; otherwise `✓ Connected. ...`, worded for the page's status (active / paused / still pending a pin). |
| `/stop` | none | Set this chat's page `status` to `stopped`, releasing its sources to the retirement sweep. | `Stopped. Your page is closed and its gauges released. Send /start whenever you want to set up a new one.`, or `NO_PAGE`. |
| `/subscribe` | `<page code>` (a page's `public_token`), or none | With a code: subscribe this chat to that page's alerts. Without: subscribe to global broadcast announcements. | With code, found: `✓ You'll get alerts for <page name>.`; not found: `I couldn't find a page with that code. Check the link your page owner sent you.` Without a code: `✓ Subscribed to broadcast announcements. To get alerts for a specific river page, send /subscribe <page code> — find the code on your page's web address.` |
| `/unsubscribe` | `<page code>`, or none | With a code: leave that one page. Without: leave every page subscription and the global broadcast list. | With code, found: `You'll no longer get alerts for <page name>.`; not found: `I couldn't find a page with that code. Check the link your page owner sent you.` Without a code, with pages: `You have been unsubscribed from broadcast announcements and from: <page names>.`; with none: `You have been unsubscribed.` |
| callback `rm:usgs:<site_id>` / `rm:noaa:<gauge_id>` | `usgs:<site_id>` or `noaa:<gauge_id>` | Unlink that USGS site or NOAA gauge from the chat's page. | `✓ Removed.\n` followed by the updated `/sources` listing, or `NO_PAGE`, or `I didn't understand that button.` on malformed data. |
| callback `sens:<level>` | `<level>` (`floods`, `unusual`, or `all`) | Set the chat's page `sensitivity` to `<level>`. | `✓ Sensitivity set to: <level label>`, or `NO_PAGE`, or `I didn't understand that button.` for an unrecognised level. |

Replies that need a link require `public_base_url`; the bot stores its own username in `telegram_bot_username` at startup for the `/start` deep link.

Verified against commit c12d91c
