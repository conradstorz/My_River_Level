# NOAA flood categories

NOAA's National Water Prediction Service publishes up to four stage
thresholds for a gauge — action, minor flood, moderate flood, and major
flood — fetched once when the gauge is added and stored on its `noaa_gauges`
row. `NoaaPollingThread` re-fetches the gauge's current stage every poll and
turns those two things into the label a subscriber actually sees.

`classify_noaa_condition` does the mapping: it checks the thresholds from the
top down, so a stage at or above `major_flood_stage` is Major, otherwise at
or above `moderate_flood_stage` is Moderate, otherwise at or above
`minor_flood_stage` is Minor, otherwise at or above `action_stage` is Action,
and anything lower is Normal. The category is always the *highest* threshold
the current stage meets, never a running tally of which ones it has passed.
When the current stage itself could not be fetched, the category is Unknown
rather than reusing the last known value — a missing reading says nothing
about where the river actually is right now.

Not every gauge publishes all four thresholds. A missing upper threshold
simply removes that category from consideration for that gauge: a gauge with
no `major_flood_stage` on file can still reach Moderate, Minor, or Action,
but the classifier will never call it Major, since there is nothing to
compare against. This is a property of the gauge's own NWPS metadata, not a
guess the classifier makes on its behalf.

Every successful poll is archived to `noaa_observations`, whether or not the
category changed — the reading is what later lets a forecast be graded
against what the river actually did, which is the whole subject of
[`gauge-quality-grading.md`](gauge-quality-grading.md). A `noaa_transition`
notification, by contrast, is enqueued only when the new category differs
from the gauge's previously stored severity; a gauge that stays Action for
days keeps writing observations every poll without saying anything new to
subscribers (its persistence is instead covered by the reminder mechanism
documented for USGS sites — NOAA gauges have no equivalent reminder).

A NOAA gauge is polled once per cycle regardless of how many landing pages
link to it: `NoaaPollingThread` iterates the distinct rows in `noaa_gauges`,
not the rows in `page_noaa_gauges`, so ten pages watching the same gauge
still cost one NWPS request, and every one of their subscribers is notified
from that single evaluation (see
[`alert-routing-and-sensitivity.md`](alert-routing-and-sensitivity.md) for
how the fan-out to subscribers works). Gauges the retirement sweep has
deactivated — `active=0` — are skipped by the same query, so a gauge nobody
references any more stops costing polls without its row, history, or grade
being deleted.
