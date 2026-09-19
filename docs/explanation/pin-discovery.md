# Pin discovery

When a visitor drops a pin on the map, `monitor/pin_discovery.py`'s
`discover` turns that lat/lon into the list of USGS and NOAA gauges shown on
the confirm screen. It prefers to reason hydrologically — find the actual
river channel and walk it — and only falls back to a plain radius search
when that is not possible. The module touches no database and every network
step degrades to "nothing from that step" on failure rather than raising, so
one dead upstream API narrows the results instead of breaking the pin flow.

## Snapping

The first step asks the USGS Network Linked Data Index (NLDI) to snap the
pin to the nearest NHDPlus flowline (`comid/position`). If the pin is
farther than 2 km from that flowline, the snap is treated as off-network —
the pin is probably not actually on a mapped waterway — and discovery falls
straight to the bounding-box search below rather than walking a channel the
visitor likely didn't mean.

## Walking the stem

A pin close enough to a flowline gets its stem walked in both directions:
NLDI's `navigation/UM/nwissite` (upstream) and `navigation/DM/nwissite`
(downstream) list the USGS gauges within `discovery_reach_km` of the snap
point along that channel. This follows the actual river and excludes
tributaries, which a plain radius search cannot do.

## Parameter check

Every USGS candidate found this way is checked against `seriesCatalogOutput`
to confirm which parameter it actually reports, preferring gauge height
(`00065`) and falling back to discharge (`00060`). This check is a hard
requirement, not an optimization: guessing a parameter code for a site that
does not report it would provision a gauge that polls forever and never
produces a reading. If the check itself fails outright, every USGS candidate
for that discovery is dropped rather than guessed at; NOAA candidates from
the bounding-box listing are unaffected and are still proposed.

## Pairing NOAA gauges

Each surviving USGS candidate is looked up on NWPS's per-gauge endpoint,
which resolves a USGS site number to its NOAA LID; a match inherits the
USGS site's upstream/downstream tag. This lookup is necessary because NWPS's
bounding-box gauge listing does not publish a `usgsId` on its entries at
all, verified live on 2026-09-17, so pairing a USGS site to its NOAA
counterpart has no route through the listing and must go through the
per-gauge endpoint instead. `monitor/noaa_client.py`'s `gauges_near` is what
queries that same bounding-box listing one more time, to add any other NWPS
gauges in range that were not already matched, tagged "nearby" since they
are not confirmed to sit on the same stem as the pin.

## Fallback

When the snap fails, lands off-network, or the stem walk turns up nothing,
discovery falls back to a bounding-box USGS site search around the pin
using `search_radius_miles`, tagging every result "nearest". This is the
same radius-based approach the admin Sites-page search uses, and it
guarantees the confirm screen is never empty for a reason the visitor can't
see.

## River name

NLDI's `comid/position` snap returns no GNIS river name — the maintainer
also checked the separate `comid/{comid}` lookup and confirmed it carries no
name either, live on 2026-09-17 — so the name shown on the confirm screen is
derived instead from the names of the stem gauges themselves, preferring
NOAA names over USGS ones since NWPS's are cleaner.
The derivation looks for a location marker — words like " AT ", " NR ", " US
OF ", or a comma — that separates a river name from the place description
that follows it, and takes everything before that marker as the name. A
short table of whole-word abbreviations is then expanded and the result
title-cased, so "OHIO R US OF MCALPINE DAM" becomes "Ohio River". "Nearby"
and "nearest" candidates are excluded from this derivation since they are
not necessarily on the same river as the pin.

Across all of the above, candidates are sorted upstream first, then
downstream, then nearby/nearest, and within each tag by distance from the
pin — so the confirm screen always leads with whatever is actually on the
same stretch of river the visitor pointed at.
