# Add a Site by Gauge Name — Design

**Date:** 2026-07-18
**Status:** Approved

## Goal

Let a user add a monitored site by typing a **gauge name** instead of only a
USGS site number. When the name matches multiple gauges, present a dropdown of
matches to pick from. The existing "add by USGS number" path is kept.

## Data source

The USGS **Monitoring Locations OGC API** supports nationwide substring search
by station name — the older `dataretrieval` NWIS site service does not.

- Endpoint: `https://api.waterdata.usgs.gov/ogcapi/v0/collections/monitoring-locations/items`
- Query over HTTPS with `requests` (already a dependency).
- Verified: `filter=monitoring_location_name LIKE '%OHIO RIVER AT LOUISVILLE%'`
  returns `03294500 — OHIO RIVER AT LOUISVILLE, KY` (Stream, Kentucky).

The `dataretrieval` calls used for polling (`get_iv`/`get_dv`) and for
number validation (`get_info`) are unchanged.

## Backend — `monitor/site_search.py` (new module)

One focused function:

```python
search_sites_by_name(query, limit=25) -> (matches, truncated, error)
```

- `matches`: list of dicts `{number, name, state, site_type}`, sorted by name,
  at most `limit` entries.
- `truncated`: `True` if more than `limit` matches exist.
- `error`: `""` on success, else a human-readable message.

Behavior:

- Build a CQL2 text filter (`filter-lang=cql2-text`):
  name `LIKE '%<QUERY>%'` **AND** site type in the relevant set
  (Stream and Lake/Reservoir).
- **Uppercase** the query before building the filter — USGS names are
  uppercase and CQL2 `LIKE` is case-sensitive.
- **Escape** user input so it can't break or inject into the filter: escape
  single quotes and the `LIKE` wildcards (`%`, `_`) so they are treated as
  literals.
- Request `limit + 1` rows to detect truncation; return the first `limit`.
- Set a request timeout (~15 s). Catch all network/HTTP/timeout errors and
  return them via `error` — the function never raises into the request handler.

Exact API property names (`monitoring_location_number`,
`monitoring_location_name`, `site_type`, `state_name`) and the `site_type`
enum strings will be confirmed against the collection schema during
implementation; the return shape above will not change.

## Routes — `web/routes.py`

- **New:** `POST /sites/search`
  - Reads `gauge_name` from the form; whitespace-only → flash validation error.
  - Calls `search_sites_by_name`.
  - `error` → flash the error, re-render the page.
  - 0 matches → flash `No gauges found matching '<query>'.`
  - ≥1 match → re-render `sites.html` with `matches`, `query`, `truncated`.
- **Unchanged:** `POST /sites/add`
  - Still accepts `site_number` + `parameter_code`, validates via the existing
    `validate_usgs_site`, and inserts. The results dropdown submits the chosen
    site number here, so there is one authoritative add path.

## UI — `web/templates/sites.html`

Three stacked blocks:

1. **Find by gauge name** — text input (`gauge_name`) + Search button, posts to
   `/sites/search`.
2. **Search results** (rendered only when `matches` is non-empty) — a `<select
   name="site_number">` whose options read `NAME — NUMBER, STATE` with value =
   site number, plus the parameter-code dropdown and an "Add selected" button
   posting to `/sites/add`. A single match is a one-option dropdown the user
   confirms. When `truncated`, show "Showing first 25 — refine your search
   (add a state or town)."
3. **Add by USGS number** — the existing form, unchanged.

## Error handling

- API failure/timeout → flash a friendly message, stay on the page; no crash,
  no partial add.
- Empty/whitespace name → validation flash.
- Add path keeps its existing validation, so a bad/removed number is still
  rejected.

## Testing

- `tests/monitor/test_site_search.py` — mock `requests`:
  single match, multiple matches, truncation (>25), zero matches, HTTP error,
  timeout, and input escaping/uppercasing (quotes and wildcards).
- `tests/web/` sites route tests — mock `search_sites_by_name`:
  search renders the dropdown, zero-match flash, API-error flash, and the
  results form adds through `/sites/add`.

## Out of scope (YAGNI)

- No live JavaScript autocomplete.
- No parameter-availability pre-check (if a chosen site lacks the selected
  parameter, polling simply returns no data, as today).
- No map or geographic UI. Nationwide name search only; refining by state/town
  means adding those words to the name box.
