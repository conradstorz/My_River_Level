# Architecture

River Monitor is one process running several daemon threads around a shared
PostgreSQL database and a single in-memory `notification_queue`. Three
pollers watch the outside world: `PollingThread` re-fetches USGS sites,
`NoaaPollingThread` re-fetches NOAA gauge stages, and `ForecastPollingThread`
archives NOAA's published forecasts and re-grades every gauge's prediction
accuracy. A fourth thread, `SchedulerThread`, watches the database rather than
an external API — it re-enqueues reminder alerts for conditions that are still
active, and once an hour it sweeps stale pin-onboarding data. All four put
items on the same queue when something is worth telling a subscriber about;
none of them talk to a notification channel directly.

```
 PollingThread  NoaaPollingThread  ForecastPollingThread  SchedulerThread
 (USGS)         (NOAA stage)       (forecast + grading)   (reminders, sweep)
      \               |                    |                    /
       \  transition  |  noaa_transition   | (writes grades)    / reminder
        \    trend    |                    |                   /
         v            v                    v                  v
                     notification_queue  <-------------------
                               |
                               v
                    NotificationDispatcher
                       /     |     |     \
                Telegram   SMS  WhatsApp Facebook

     PostgreSQL  <----- every thread above ----->  Flask (waitress)
   (settings, sites, gauges,                     portal UI + webhooks
    subscribers, notifications, pages)
```

`monitor/dispatcher.py`'s `NotificationDispatcher` is the only thread that
reads the queue; it blocks on `queue.get(timeout=1)`, so it reacts within a
second of anything being enqueued, looks up which page subscribers are
allowed to hear about that item, and calls the adapter for each recipient's
channel. The portal and the inbound webhooks live on a fifth thread, a Flask
app served by waitress rather than Flask's own development server, because
the latter is not built to stay up under continuous production use.

Everything a poller or the portal needs to behave differently — thresholds,
poll interval, reminder cadence, channel credentials — is a row in the
`settings` table, not a config file. A thread reads its settings fresh on
every cycle, so changing `poll_interval_minutes` or a Twilio token on the
Settings page takes effect on the next loop, no restart required. Admin
credentials are the deliberate exception: `ADMIN_USERNAME` and
`ADMIN_PASSWORD_HASH` come from the environment only. Storing them in the
database would be circular — the database is exactly what the Basic-auth
guard on the portal is protecting, so the credentials that unlock it cannot
also live inside it.

`main.py` wires all of this together: it initializes the database, builds
the notification adapters, starts every worker thread plus the web thread,
and then supervises them. Every 60 seconds it checks which threads are still
alive; if one of `PollingThread`, `NoaaPollingThread`, `ForecastPollingThread`,
`SchedulerThread`, `NotificationDispatcher`, or `WebThread` has died, it exits
the process non-zero so Docker's `restart: unless-stopped` policy brings a
working container back, rather than leaving one that looks "Up" while it has
quietly stopped monitoring anything. See [`threads.md`](../reference/threads.md)
for each thread's interval and failure handling, and [`settings.md`](../reference/settings.md)
for every setting and which thread reads it.
