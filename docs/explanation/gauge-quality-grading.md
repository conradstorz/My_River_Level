# Gauge quality grading

A gauge having flood-stage thresholds only means NOAA *could* tell a
subscriber when flooding starts — it says nothing about whether the
forecasts leading up to that moment are any good. `monitor/gauge_quality.py`
answers the real question by comparing forecasts archived earlier against
what a gauge's own observations later showed actually happened, and turning
the comparison into a letter grade with a plain-English explanation.

Archiving is what makes grading possible at all: a forecast's accuracy can
only be judged after the moment it described has passed. Every
`forecast_poll_hours` (six by default — see
[`settings.md`](../reference/settings.md)), `ForecastPollingThread` fetches
each active gauge's published forecast, stores its points in
`gauge_forecasts`, and then re-grades every gauge in one pass, so a grade
never lags more than one forecast cycle behind the observations that would
change it.

Grading pairs each archived forecast point with the observation it can be
checked against: a point counts toward a horizon (24, 48, or 72 hours) when
its lead time — the gap between when NOAA issued it and the moment it
predicts — falls within 90 minutes of that horizon, and it is then compared
against whichever archived observation is closest to that predicted moment,
again within 90 minutes. A point with no observation that close is skipped
outright, so the sample count reported to a subscriber only counts pairs
that were actually verifiable. The mean absolute error of the 24-hour pairs
is what decides the letter grade; 48- and 72-hour error is computed the same
way and reported alongside it, but does not affect the grade itself.

The rubric is checked in order, first match wins. No flood-stage thresholds
at all is graded F ("Not usable for flood warning") — regardless of forecast
quality, the gauge cannot say when flooding starts. A gauge with thresholds
but no confirmed forecast is D ("Observation only"). No observation in the
last 24 hours is F ("Not reporting"), even if forecasts exist, because a
forecast nobody can check against reality is not worth grading. Fewer than
ten matched pairs is not a letter at all but "Unrated" ("Collecting accuracy
data") — not enough evidence yet either way. Only once ten or more pairs
exist does the 24-hour mean absolute error decide the letter: under 0.5 ft
is A ("Reliable flood predictions"), under 1.0 ft is B ("Good"), under 2.0 ft
is C ("Fair"), and 2.0 ft or worse is D ("Unreliable"). The headline in
parentheses and a longer sentence citing the actual error figures are stored
together in one text column, joined by a separator, and split back apart
for display.

`has_forecast` on `noaa_gauges` is a deliberate three-way flag rather than a
boolean: true once NOAA has confirmed a forecast exists, false once NOAA has
confirmed it does not (an HTTP 404, or a 200 with no readings), and left
`NULL` for a gauge that has never been successfully checked. Only those two
confirmed outcomes ever set the flag; a network failure or an unparseable
response leaves it exactly as it was. Collapsing a failed fetch into false
would tell a subscriber "this gauge gives no advance warning" when the truth
is only "we could not ask NOAA this time" — a much stronger and more
damaging claim than the situation warrants.

"Unrated" paired with "Not yet assessed" is the ordinary state for a gauge
that was just added, not a fault to be fixed: the forecast archive starts
empty and takes days to accumulate the ten matched pairs a letter grade
needs, and a portal page showing a freshly added gauge is expected to show
that headline until enough forecasts have come and gone to check against.
