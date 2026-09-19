# USGS classification

`monitor/polling.py`'s `fetch_and_evaluate_site` is what turns one USGS
reading into everything downstream of it: a percentile, a severity, a
direction, and — when any of those changed — a queued alert. It runs once per
active site on every `PollingThread` cycle, and it is worth reading start to
end once, because every other page in this section (reminders, trend alerts,
alert routing) assumes the values it computes.

## Percentiles

A site's percentile is the fraction of its own daily-mean history that sits
below the current reading: the site's daily-mean values since
`historical_start_year` are counted, and the percentile is how many of them
fall below the newest interval reading, expressed out of 100. A gauge with a
longer, more stable history yields a more stable percentile; a newly added
gauge or one with sparse history can swing more per reading simply because
the denominator is small.

## Severity bands

`classify_condition` maps that percentile to one of five bands using four
settings: at or below `very_low_percentile` is SEVERE LOW, at or below
`low_percentile` is LOW, at or above `very_high_percentile` is SEVERE HIGH,
at or above `high_percentile` is HIGH, and anything between is NORMAL. See
[`settings.md`](../reference/settings.md) for the default values and which
thread reads each one. A percentile of `None` — which reaching this function
implies the caller could not compute one — classifies as UNKNOWN rather than
guessing a band.

## Direction

Alongside severity, `fetch_and_evaluate_site` compares the new reading to the
site's previous stored value and labels it RISING, FALLING, or STEADY. This
is purely reading-over-reading: it says nothing about rate, only which way
the last two points moved, and it feeds the arrow in a transition message
(see [`alerts.md`](../reference/alerts.md)).

A first reading for a site has no previous severity to compare against.
`detect_transition` treats that as "nothing changed" rather than announcing
`None → NORMAL`, so adding a new site never fires a spurious first alert —
the first row in `site_conditions` is recorded silently, and only the second
poll onward can produce a transition.

## Reminders

A severity that persists doesn't repeat its transition alert; instead
`SchedulerThread` re-checks every active site's current severity every five
minutes and enqueues a `reminder` item when one is due. The interval depends
on the severity: `reminder_severe_hours` for SEVERE LOW/SEVERE HIGH,
`reminder_low_high_hours` for LOW/HIGH, and never for NORMAL. "Due" means no
`reminder`-type row exists yet for that site in `notifications`, or the most
recent one is older than the interval — and `is_reminder_due` looks up that
most recent row with no severity filter, so the clock is per site, not per
episode. A condition that flips back to NORMAL and returns to, say, HIGH does
not start the clock over: its first reminder in the new episode is still due
relative to the old episode's last reminder, so it can arrive sooner than a
full interval away, or immediately if that old reminder is already older
than the interval.

## Rise and fall alerts

Percentile bands only fire when a river leaves its historical normal range,
which can stay silent for days while the water still moves several feet.
`monitor/trend.py` catches that: it looks at every `site_conditions` row
within `rate_change_window_hours`, and if the site moved far enough it
queues a `trend` message. "Far enough" means at least `rate_change_threshold_ft`
for gauge height (parameter `00065`) or `rate_change_threshold_pct` of the
window's starting value for anything else (discharge, mainly), and a trend
alert only fires if `rate_change_min_interval_hours` have passed since the
last one for that site, so a multi-day rise doesn't re-alert every poll.

## Worked example

Take the Willamette River at Portland (`#14211720`), discharge 45230 cfs.
Suppose 10,000 daily-mean values exist since `historical_start_year`, and
9,130 of them are below 45230 cfs: the percentile is `9130/10000*100 = 91.3`.
With the default thresholds (`high_percentile=90`, `very_high_percentile=95`)
91.3 falls at or above 90 but below 95, so `classify_condition` returns HIGH.
If the site's previous stored severity was NORMAL, that is a transition
(NORMAL → HIGH) and, if the previous reading was lower, direction RISING —
together producing the transition message shown in
[`alerts.md`](../reference/alerts.md). While the site stays HIGH,
`SchedulerThread` repeats a reminder every `reminder_low_high_hours` (24 by
default). Separately, if the same site's gauge height rose 2.3 ft in the
last 4 hours — inside the default 6-hour `rate_change_window_hours` and past
the default 2.0 ft `rate_change_threshold_ft` — `monitor/trend.py` queues a
`trend` alert too, independent of whether the percentile band changed.
