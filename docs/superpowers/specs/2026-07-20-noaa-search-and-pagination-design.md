# NOAA Name Search + Paginated Gauge Results

**Date:** 2026-07-20
**Status:** Approved design — ready for implementation planning
**Builds on:** 2026-07-20-gauge-discovery-design.md

## Problem

Two gaps in gauge discovery:

1. **NOAA names aren't searchable.** USGS and NOAA name the same gauge
   differently (USGS: `OHIO R US OF MCALPINE DAM`; NOAA: `McAlpine Upper`).
   The search only queries USGS names, so a user who knows the NOAA/common
   name can't find the gauge. The landing-page editor's "NOAA search" actually
   searches USGS names and bridges by `usgsId`, so it has the same blind spot.
2. **Results cap at 25 with no paging.** The search shows the 25 best matches
   and a "truncated" hint; there is no way to see matches 26+.

## Key technical facts (verified live, 2026-07-20)

- NOAA's **`water/riv_gauges` ArcGIS MapServer** is queryable by attribute.
  Layer 0 ("Observed River Stages") query
  `UPPER(location) LIKE '%MCALPINE%'` returns `MLUK2 "McAlpine Upper"` and
  `MLPK2 "McAlpine Lower"` (waterbody `Ohio River`, state `KY`), each with its
  `gaugelid`. Fields include `gaugelid, location, waterbody, state, latitude,
  longitude`. Endpoint:
  `https://mapservices.weather.noaa.gov/eventdriven/rest/services/water/riv_gauges/MapServer/0/query`
  (`where`, `outFields`, `returnGeometry=false`, `f=json`,
  `resultRecordCount`). It supports server-side paging, but we cap locally.
- The layer does **not** expose `usgsId`. To map a NOAA hit to a USGS site,
  call `fetch_gauge_metadata(lid)` (NWPS `/gauges/{lid}`), whose response
  already carries `usgsId`.
- The NWPS `/gauges` list endpoint is unusable for bulk discovery (ignores
  bbox, returns an arbitrary 2-item page) — the ArcGIS service is the source.

## Decisions (from brainstorming)

- **NOAA search scope:** fix the landing-page editor to search NOAA names
  directly, AND add source-labeled NOAA matches to the Sites page.
- **Pagination model:** ranked pages over a capped pool (~300 best), 25 per
  page, best matches always first; a query that hits the cap shows a "refine"
  note.
- **Search is GET** (`?q=…&page=2`) — bookmarkable, Prev/Next are plain links.
- **Page size:** 25.

## Components

### 0. `monitor/search_text.py` (new) — shared ranking helpers

Extract `_tokenize(query)` and `_score(name, tokens)` (currently private in
`site_search.py`) into a shared module so `noaa_search.py` reuses them instead
of duplicating. `site_search.py` imports them from here; its existing tests
that reference `_tokenize`/`_score`/`_match_group` keep working (re-export the
names it still owns).

### 0b. `monitor/site_search.py` (contract change)

`search_sites_by_name(query, cap=300) -> (candidates, capped, error)` now
returns the **full ranked pool** (up to `cap`), not `candidates[:25]`, and a
`capped` flag (pool reached `cap`) instead of the old `truncated` (>25) flag —
pagination happens above it. All existing relaxation/ranking logic (Steps 1–3)
is unchanged; only the return slice/flag changes. The `/sites` route and this
function's tests are updated to the new contract.

### 1. `monitor/noaa_search.py` (new)

`search_noaa_gauges_by_name(query, cap=300) -> (candidates, capped, error)`

- Tokenizes and ranks with `_tokenize`/`_score` imported from
  `monitor/search_text.py` (component 0).
- Builds an ArcGIS `where`: for each keyword, `(UPPER(location) LIKE '%KW%' OR
  UPPER(waterbody) LIKE '%KW%')`, AND-ed. Single quotes doubled; `%`/`_`
  stripped from keywords (same escaping discipline as the USGS CQL path).
- Fetches up to `cap` features, ranks locally with `_score` against
  `"<location> <waterbody> <state>"`, returns the ranked pool.
- `candidates`: list of `{lid, name, waterbody, state}` (`name` = `location`).
- `capped`: True if the fetch returned `cap` features.
- Best-effort: any HTTP/parse failure returns `([], False, message)`, never
  raises.

### 2. `monitor/noaa_client.py` (extend)

`fetch_gauge_metadata` return dict gains `usgs_id` (str or None), read from
the NWPS response's `usgsId`. Existing keys unchanged; additive.

### 3. `monitor/gauge_discovery.py` (new) — Sites combined pool

`combined_site_matches(query) -> (rows, noaa_only_count, capped, error)`

- Runs `search_sites_by_name(query)` (USGS) and
  `search_noaa_gauges_by_name(query)` (NOAA), each best-effort.
- For each NOAA candidate, resolves `usgs_id` via `fetch_gauge_metadata(lid)`
  (concurrent, cached — mirror `annotate_noaa`'s pool/cache pattern).
- Produces ONE ranked list of addable USGS-site rows keyed by site number:
  - USGS candidates contribute their `{number, name, state, site_type}`.
  - A NOAA candidate with a `usgs_id` contributes/annotates that number with
    `noaa_name` (its NOAA `location`) and `noaa_lid`; if the number is new, it
    becomes an addable row labeled by its NOAA name.
  - Dedup by site number (a gauge found both ways is one row carrying both).
- `noaa_only_count`: NOAA hits with no `usgs_id` (not addable on the Sites
  page; surfaced as a note, not as rows).
- `capped`: True if either source hit its cap.
- Cached by normalized query (short TTL, ~120s) so paging reuses the pool.

### 4. `monitor/search_cache.py` (new) — paging cache

`get_or_compute(key, producer, ttl=120)` — a tiny in-process TTL cache so a
GET page request reuses the previously-built ranked pool instead of re-hitting
the APIs on every Prev/Next. Used by `combined_site_matches` and the editor
NOAA search.

### 5. `web/routes.py` + templates — pagination

- `search_sites` becomes **GET** `/sites/search?q=…&page=N`:
  - Builds/uses the cached combined pool, computes `total`, `pages`, slices the
    25 rows for `page`, runs `annotate_liveness` (and `annotate_noaa` for rows
    lacking NOAA info) on that page only, renders the table with a pager
    ("Page X of Y · N matches", Prev/Next), the `capped` "refine" note, and a
    `noaa_only_count` note when > 0.
- Landing-page editor: `page_search_gauges` becomes **GET**
  `/edit/<edit_token>/gauges/search?q=…&page=N`, calling
  `search_noaa_gauges_by_name` directly (paginated), listing NOAA gauges by
  their real names; add-by-LID unchanged.
- `sites.html` / `page_edit.html`: search forms use `method="get"`; add pager
  controls and the NOAA source tag / notes. NOAA-tagged rows show
  "matched NOAA: <name>" alongside the existing 🌊 badge.

## Data flow (Sites page)

```
GET /sites/search?q=mcalpine upper&page=1
  → combined_site_matches("mcalpine upper")   (cached)
      USGS search → ranked USGS candidates
      NOAA search → NOAA hits → resolve usgs_id (concurrent, cached)
      merge + dedup by site number → ranked rows (+ noaa_only_count, capped)
  → slice page 1 (rows 1–25)
  → annotate_liveness + annotate_noaa on those 25
  → render table + pager + notes
```

## Error handling

- NOAA source failure → USGS results still show (and vice-versa); the failed
  source contributes nothing, no user-facing error unless BOTH fail.
- `usgs_id` resolution failure for a NOAA hit → that hit is treated as
  NOAA-only (counted, not an addable row); never raises.
- Enrichment stays best-effort per the prior design.

## Testing

- `search_noaa_gauges_by_name`: mock the ArcGIS HTTP — keyword `where`
  construction, ranking order, `{lid,name,...}` shape, `capped` flag, and
  graceful failure (empty + message, no raise). A `MCALPINE` fixture asserts
  `MLUK2` ranks for `"mcalpine upper"`.
- `fetch_gauge_metadata`: returns `usgs_id`; existing keys preserved.
- `combined_site_matches`: USGS-only, NOAA-only-with-usgsid (becomes addable
  row), both-sources dedup to one row, NOAA-without-usgsid increments
  `noaa_only_count`; source failures degrade.
- `search_cache.get_or_compute`: caches within TTL, recomputes after; distinct
  keys isolated.
- Routes: GET search renders page 1 with a pager; `page=2` shows rows 26–50;
  NOAA-tagged row renders its tag; editor GET search finds a NOAA gauge by
  NOAA name; both degrade when a source is mocked to fail.

## Out of scope (YAGNI)

- No merged single-search rewrite; the two pages keep their existing purposes
  (Sites = USGS flow-alert sites; editor = NOAA flood gauges).
- No unbounded server-side paging; the pool is capped at ~300 best.
- No persistent NOAA catalog cache beyond the short-TTL paging cache.
- No cross-page global re-ranking (ranking is over the capped pool).
