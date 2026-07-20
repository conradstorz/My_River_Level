# Reliable, Unified Gauge Discovery (USGS + NOAA)

**Date:** 2026-07-20
**Status:** Approved design — ready for implementation planning

## Problem

Users can't reliably find and add working water-level gauges:

1. **Dead USGS gauges.** The USGS name search (`monitor/site_search.py`,
   Sites page) surfaces long-discontinued gauges. They add fine but never
   report live data, so the site looks broken.
2. **NOAA gauges are undiscoverable.** NOAA gauges can only be added in the
   landing-page editor by typing a NOAA LID (e.g. `MLUK2`) from memory. There
   is no search; a wrong guess just fails with "not found."

The user confirmed these are the two symptoms (not a need for new data
providers, and not a wrong-parameter problem).

## Key technical facts (verified against live APIs, 2026-07-20)

- The USGS Monitoring Locations OGC API name search already works well and is
  ranked (`monitor/site_search.py`).
- USGS **liveness / last value** is obtainable with one batch
  `nwis.get_iv(sites=[...], period=short)` call for the handful of results
  actually shown.
- NOAA NWPS has **no reliable name/bbox search endpoint** (the `/gauges` list
  and bbox filters return empty or error).
- **Bridge:** `GET https://api.water.noaa.gov/nwps/v1/gauges/{id}` accepts
  *either* a NOAA LID *or* a USGS site number. Passing USGS `03293551` returns
  NOAA gauge `MLUK2` with its `lid`, `name`, current `status.observed`
  (`primary` stage + `validTime`), and `flood.categories`.

This bridge means the existing USGS name search can be the single entry point
for both sources — no fragile caching of a full NOAA gauge list.

## Approach (chosen)

**Unified discovery, reused everywhere.** One ranked name search (the existing
USGS search) feeds two best-effort enrichment passes. The enriched results
appear in **both** the Sites page and the landing-page editor. The two alert
subsystems stay distinct — USGS drives flow-percentile alerts; NOAA drives
flood-category alerts — but *discovery* is one shared, honest path.

Rejected alternative: improving each search in place separately (two
independent search widgets). More UX surface to maintain, less code reuse.

## Components

### 1. `monitor/gauge_enrich.py` (new)

Enrichment layer, kept out of `site_search.py` so ranking stays focused. Every
function is best-effort and never raises out to the caller.

- `annotate_liveness(matches)` — one batch `nwis.get_iv` call for all shown
  site numbers over a short recent window (~5 days). For each match, add:
  - `live` (bool) — a reading exists within the window
  - `last_value` (float or None), `last_unit` (str), `last_time` (datetime/str)

  On any failure, leave these fields unset and return the matches unchanged.

- `annotate_noaa(matches)` — concurrent `GET /nwps/v1/gauges/{usgs#}` lookups
  (small thread pool, short per-request timeout, in-memory TTL cache keyed by
  site number). For each match where a co-located forecast gauge exists, add:
  - `noaa_lid` (str), `noaa_has_flood` (bool), `noaa_category` (current flood
    category label, if available)

  A 404 means "no NOAA gauge here" (fields stay empty). Timeouts/errors are
  swallowed per-item; the search result is still shown without the badge.

### 2. `monitor/noaa_client.py` (extend)

- `fetch_gauge_metadata(identifier)` also returns the resolved **`lid`** and
  accepts *either* a USGS site number *or* a NOAA LID (the API already
  resolves both). This is what lets the landing-page editor turn a name-search
  pick into a storable LID. Existing return keys (`station_name`, threshold
  fields) are preserved; `lid` is added.

### 3. `web/routes.py`

- `/sites/search` (POST) — after ranking, run `annotate_liveness` then
  `annotate_noaa` on the shown results; pass enriched matches to the template.
- Landing-page editor gains `/edit/<edit_token>/gauges/search` (POST) — same
  name search + NOAA resolution, re-renders the editor with named results.
- `page_add_gauge` accepts the chosen USGS number or LID, resolves it via
  `fetch_gauge_metadata`, and stores the real LID (existing
  `get_or_create_noaa_gauge` / `link_page_gauge` flow, unchanged downstream).

### 4. Templates

- `web/templates/sites.html` — replace the plain `<select>` of matches with a
  **results table**: name · USGS # · liveness badge
  (`Reporting · 12.8 ft · 2h ago` vs `No recent data · last 2019`, live sorted
  first) · NOAA badge (`🌊 flood forecast`) · per-row Add button + parameter
  picker.
- `web/templates/page_edit.html` — replace the blind LID input with the same
  name-search box plus a results list you add gauges from by name.

## Data flow

```
query
  → ranked USGS candidates (site_search.search_sites_by_name)
  → take top N shown
  → parallel enrich: 1 batch USGS-IV call + N cached NOAA lookups
  → render results table with liveness + NOAA badges
```

## Error handling

Enrichment is strictly additive and best-effort:

- USGS-IV batch failure → no liveness badges; search still works.
- NOAA 404 → "no NOAA gauge here" (normal, silent).
- NOAA timeout/error → no NOAA badge for that row; no user-facing error.
- Nothing in enrichment can turn a working search into a failed search.

## Testing

- `annotate_liveness`: recent value → `live` true with value/time; stale or
  empty frame → `live` false. Mock `nwis.get_iv`. Mock a raise → matches
  returned unchanged.
- `annotate_noaa`: 200 with `flood.categories` → `noaa_has_flood` true +
  `noaa_lid`; 404 → empty fields; timeout → empty, no raise.
- `fetch_gauge_metadata`: resolves an LID from a USGS number; return dict
  includes `lid` and preserves existing keys.
- Routes: `/sites/search` renders liveness + NOAA badges; renders gracefully
  when enrichment is mocked to fail; landing-page name search resolves a pick
  to an LID and adds the gauge.

## Out of scope (YAGNI)

- No new data providers beyond USGS and NOAA.
- No merging of the two alert subsystems (flow-percentile vs. flood-category).
- No persistent/full NOAA gauge-list cache — the `usgsId` bridge avoids it.
