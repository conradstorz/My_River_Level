# NOAA Name Search + Paginated Gauge Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users find gauges by their NOAA names (not just USGS names) and page through all ranked matches, on both the Sites page and the landing-page editor.

**Architecture:** Add a NOAA name search backed by NOAA's `water/riv_gauges` ArcGIS service; share the fuzzy ranking helpers between the USGS and NOAA searches; combine both sources into one ranked list of addable USGS sites for the Sites page (NOAA hits resolved to their USGS site via `usgs_id`); paginate a ranked, capped pool (25/page) cached across GET page requests.

**Tech Stack:** Python 3, Flask, Jinja2, `requests`, `dataretrieval`, pandas, PostgreSQL, pytest.

## Global Constraints

- Never chain shell commands (`&&`, `||`, `|`, `;`). One command per Bash call.
- **Tests run only in Docker** (plain `pytest` can't reach the DB). Focused run:
  `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest <path> -v`
  Full suite: same command without `<path>`. `--build` picks up source edits.
- Every network call is best-effort: a NOAA-source failure must not break the USGS results and vice-versa; enrichment failures never break search. Functions return `([], ..., message)` on failure, never raise to callers.
- Ranking is over a capped pool (`cap=300`); page size is 25; search is GET (`?q=…&page=N`).
- Follow existing patterns: module-level `logger`, mock `requests.get`/`nwis` at the module where used, keyword escaping doubles `'` and strips `%`/`_`.
- NOAA ArcGIS endpoint: `https://mapservices.weather.noaa.gov/eventdriven/rest/services/water/riv_gauges/MapServer/0/query` — params `where`, `outFields=gaugelid,location,waterbody,state`, `returnGeometry=false`, `resultRecordCount=<cap>`, `f=json`; response `features[].attributes.{gaugelid,location,waterbody,state}`; `exceededTransferLimit` signals capping.

---

### Task 1: Extract shared ranking helpers → `monitor/search_text.py`

**Files:**
- Create: `monitor/search_text.py`
- Modify: `monitor/site_search.py` (remove local `_STATE_ABBR`/`_tokenize`/`_score`, import them)
- Test: `tests/monitor/test_search_text.py`

**Interfaces:**
- Produces: `_tokenize(query) -> list[str]` (uppercase tokens ≥2 chars); `_score(name, tokens) -> float`; `_STATE_ABBR` dict. `site_search.py` re-exports these names (imports them) so existing `from monitor.site_search import _tokenize, _score` keeps working.

- [ ] **Step 1: Write the failing test**

Create `tests/monitor/test_search_text.py`:

```python
from monitor.search_text import _tokenize, _score, _STATE_ABBR


def test_tokenize_uppercases_and_drops_short_tokens():
    assert _tokenize("Ohio River at Louisville, KY") == [
        "OHIO", "RIVER", "AT", "LOUISVILLE", "KY"]
    assert _tokenize("a b cd") == ["CD"]


def test_score_exact_tokens_sum_to_one_each():
    assert _score("OHIO RIVER AT LOUISVILLE, KY", ["OHIO", "RIVER"]) == 2.0


def test_score_credits_state_name_via_abbreviation():
    assert _score("OHIO RIVER AT LOUISVILLE, KY", ["KENTUCKY"]) == 1.0
    assert _STATE_ABBR["KENTUCKY"] == "KY"


def test_score_ranks_typo_nearest_word_highest():
    tokens = ["LOUSVILLE", "OHIO", "RIVER"]
    assert (_score("OHIO RIVER AT LOUISVILLE, KY", tokens)
            > _score("OHIO RIVER AT CINCINNATI, OH", tokens))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_search_text.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitor.search_text'`.

- [ ] **Step 3: Create `monitor/search_text.py`**

Cut the `_STATE_ABBR` dict (currently `site_search.py` lines ~38–50), `_tokenize`, and `_score` verbatim into the new module:

```python
"""Shared text helpers for gauge name search — tokenization, full US
state-name expansion, and difflib relevance scoring. Used by both the USGS
(site_search) and NOAA (noaa_search) name searches so ranking is identical."""

import re
from difflib import SequenceMatcher

# Single-word US state names -> the abbreviation used at the end of gauge
# names (e.g. "..., KY"). Multi-word states are intentionally omitted.
_STATE_ABBR = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE",
    "NEVADA": "NV", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WISCONSIN": "WI",
    "WYOMING": "WY",
}


def _tokenize(query):
    """Split user text into uppercase keyword tokens (>= 2 chars)."""
    return [t for t in re.split(r"[^A-Z0-9]+", query.upper()) if len(t) >= 2]


def _score(name, tokens):
    """Relevance score of a station name against the query tokens.

    Each token contributes up to 1.0: 1.0 for an exact substring (or a state
    name whose ", XX" abbreviation is present), else the best difflib
    similarity of the token to any single word in the name.
    """
    up = name.upper()
    words = [w for w in re.split(r"[^A-Z0-9]+", up) if w]
    total = 0.0
    for t in tokens:
        if t in up:
            total += 1.0
            continue
        if t in _STATE_ABBR and f", {_STATE_ABBR[t]}" in up:
            total += 1.0
            continue
        total += max((SequenceMatcher(None, t, w).ratio() for w in words),
                     default=0.0)
    return total
```

- [ ] **Step 4: Update `monitor/site_search.py` to import them**

Remove the `_STATE_ABBR` dict, `_tokenize`, and `_score` definitions from `site_search.py`. It already imports `re` and `SequenceMatcher`; keep `re` (used by `_to_match`) and drop the now-unused `SequenceMatcher` import. Add near the top (after `import requests`):

```python
from monitor.search_text import _STATE_ABBR, _tokenize, _score
```

`_match_group` (which uses `_STATE_ABBR`), `_escape`, `_fetch`, `_to_match`, and `search_sites_by_name` stay in `site_search.py` and now use the imported names.

- [ ] **Step 5: Run both test files to verify they pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_search_text.py tests/monitor/test_site_search.py -v`
Expected: PASS — new helper tests pass; all existing `site_search` tests (which import `_tokenize`/`_score`/`_match_group` from `monitor.site_search`) still pass via the re-export.

- [ ] **Step 6: Commit**

```bash
git add monitor/search_text.py monitor/site_search.py tests/monitor/test_search_text.py
git commit -m "refactor: extract shared tokenize/score helpers into search_text"
```

---

### Task 2: `fetch_gauge_metadata` returns `usgs_id`

**Files:**
- Modify: `monitor/noaa_client.py` (`fetch_gauge_metadata` return dict)
- Test: `tests/monitor/test_noaa_client.py`

**Interfaces:**
- Produces: `fetch_gauge_metadata(identifier, timeout=TIMEOUT)` return dict gains `usgs_id` (str or None) from the NWPS `usgsId` field. Existing keys unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/monitor/test_noaa_client.py` (extend the existing dict-shape mock's data with a `usgsId`, or add a focused test):

```python
def test_fetch_gauge_metadata_returns_usgs_id():
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "lid": "MLUK2", "name": "Ohio River at McAlpine Upper",
        "usgsId": "03293551",
        "flood": {"categories": {"action": {"stage": 21.0}}},
    }
    with patch("monitor.noaa_client.requests.get", return_value=mock):
        meta = fetch_gauge_metadata("MLUK2")
    assert meta["usgs_id"] == "03293551"
    assert meta["lid"] == "MLUK2"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_noaa_client.py::test_fetch_gauge_metadata_returns_usgs_id -v`
Expected: FAIL — `KeyError: 'usgs_id'`.

- [ ] **Step 3: Add `usgs_id` to the return dict**

In `monitor/noaa_client.py`, in `fetch_gauge_metadata`, change the final return to include `usgs_id`:

```python
    return {
        "station_name": data.get("name", identifier),
        "lid": data.get("lid", identifier),
        "usgs_id": data.get("usgsId"),
        **thresholds,
    }
```

- [ ] **Step 4: Run the NOAA client tests**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_noaa_client.py -v`
Expected: PASS — new test passes; existing tests unaffected (additive key).

- [ ] **Step 5: Commit**

```bash
git add monitor/noaa_client.py tests/monitor/test_noaa_client.py
git commit -m "feat: fetch_gauge_metadata returns usgs_id for NOAA->USGS mapping"
```

---

### Task 3: NOAA gauge name search → `monitor/noaa_search.py`

**Files:**
- Create: `monitor/noaa_search.py`
- Test: `tests/monitor/test_noaa_search.py`

**Interfaces:**
- Consumes: `_tokenize`, `_score` from `monitor.search_text`.
- Produces: `search_noaa_gauges_by_name(query, cap=300) -> (candidates, capped, error)`. `candidates`: ranked list of `{lid, name, waterbody, state}` (`name` = ArcGIS `location`). `capped`: bool. Best-effort — `([], False, message)` on failure, never raises. Also exposes `_where(tokens)` and `_to_match(feat)` for tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/monitor/test_noaa_search.py`:

```python
from unittest.mock import patch, MagicMock
import requests
from monitor.noaa_search import search_noaa_gauges_by_name, _where, _to_match


def _feat(lid, location, waterbody="Ohio River", state="KY"):
    return {"attributes": {"gaugelid": lid, "location": location,
                           "waterbody": waterbody, "state": state}}


def _resp(features, exceeded=False):
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.return_value = {"features": features, "exceededTransferLimit": exceeded}
    return r


def test_where_ands_keywords_over_location_and_waterbody():
    w = _where(["MCALPINE", "UPPER"])
    assert w == ("(UPPER(location) LIKE '%MCALPINE%' OR UPPER(waterbody) LIKE '%MCALPINE%')"
                 " AND (UPPER(location) LIKE '%UPPER%' OR UPPER(waterbody) LIKE '%UPPER%')")


def test_to_match_shape_and_skips_missing_lid():
    assert _to_match(_feat("MLUK2", "McAlpine Upper")) == {
        "lid": "MLUK2", "name": "McAlpine Upper",
        "waterbody": "Ohio River", "state": "KY"}
    assert _to_match({"attributes": {"location": "x"}}) is None


def test_search_ranks_noaa_name_match():
    feats = [_feat("MLUK2", "McAlpine Upper"), _feat("MLPK2", "McAlpine Lower")]
    with patch("monitor.noaa_search.requests.get", return_value=_resp(feats)):
        cands, capped, err = search_noaa_gauges_by_name("mcalpine upper")
    assert err == "" and capped is False
    assert cands[0]["lid"] == "MLUK2"          # exact "upper" match ranks first
    assert {c["lid"] for c in cands} == {"MLUK2", "MLPK2"}


def test_search_reports_capped():
    feats = [_feat(f"L{i}", f"CREEK {i}") for i in range(3)]
    with patch("monitor.noaa_search.requests.get", return_value=_resp(feats, exceeded=True)):
        _cands, capped, err = search_noaa_gauges_by_name("creek")
    assert capped is True and err == ""


def test_search_survives_api_failure():
    with patch("monitor.noaa_search.requests.get",
               side_effect=requests.exceptions.Timeout("slow")):
        cands, capped, err = search_noaa_gauges_by_name("ohio")
    assert cands == [] and capped is False and "timed out" in err.lower()


def test_search_empty_query_errors_without_calling_api():
    with patch("monitor.noaa_search.requests.get") as g:
        cands, capped, err = search_noaa_gauges_by_name("   ")
    assert cands == [] and err != ""
    g.assert_not_called()
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_noaa_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitor.noaa_search'`.

- [ ] **Step 3: Create `monitor/noaa_search.py`**

```python
"""
NOAA gauge search by keywords, ranked best-first.

NWPS has no name-search API, but NWS's `water/riv_gauges` ArcGIS MapServer is
queryable by the gauge's NOAA name (`location`) and river (`waterbody`). This
lets users find a gauge by its NOAA/common name (e.g. "McAlpine Upper") even
when USGS names the same site differently. Ranking reuses the shared fuzzy
scorer so NOAA and USGS results rank consistently.
"""

import logging

import requests

from monitor.search_text import _tokenize, _score

logger = logging.getLogger(__name__)

_URL = (
    "https://mapservices.weather.noaa.gov/eventdriven/rest/services"
    "/water/riv_gauges/MapServer/0/query"
)
_OUT_FIELDS = "gaugelid,location,waterbody,state"
_TIMEOUT_SECONDS = 15
_CAP = 300


def _escape(text):
    """Escape a keyword for an ArcGIS SQL LIKE literal (strip wildcards, double quotes)."""
    return text.replace("%", "").replace("_", "").replace("'", "''")


def _where(tokens):
    """AND of per-keyword clauses, each matching the NOAA name or the river."""
    clauses = []
    for t in tokens:
        e = _escape(t)
        clauses.append(
            f"(UPPER(location) LIKE '%{e}%' OR UPPER(waterbody) LIKE '%{e}%')")
    return " AND ".join(clauses)


def _fetch(where, cap):
    """Run one ArcGIS query. Returns (features, capped, error); never raises."""
    params = {
        "where": where,
        "outFields": _OUT_FIELDS,
        "returnGeometry": "false",
        "resultRecordCount": cap,
        "f": "json",
    }
    try:
        resp = requests.get(_URL, params=params, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        logger.warning("NOAA gauge search timed out")
        return [], False, "NOAA search timed out. Please try again."
    except requests.exceptions.RequestException as e:
        logger.warning("NOAA gauge search failed: %s", e)
        return [], False, "NOAA search failed. Please try again later."
    except ValueError:
        return [], False, "NOAA search returned an unexpected response."
    if not isinstance(data, dict) or "error" in data:
        return [], False, "NOAA search returned an unexpected response."
    feats = data.get("features") or []
    if not isinstance(feats, list):
        feats = []
    capped = bool(data.get("exceededTransferLimit")) or len(feats) >= cap
    return feats, capped, ""


def _to_match(feat):
    """Convert an ArcGIS feature to a match dict, or None if unusable."""
    attrs = (feat or {}).get("attributes") or {}
    lid = attrs.get("gaugelid")
    if not lid:
        return None
    return {
        "lid": lid,
        "name": attrs.get("location") or "",
        "waterbody": attrs.get("waterbody") or "",
        "state": attrs.get("state") or "",
    }


def search_noaa_gauges_by_name(query, cap=_CAP):
    """
    Search NOAA gauges by keywords, ranked best-first.

    Returns (candidates, capped, error):
      candidates: ranked list of {lid, name, waterbody, state}
      capped:     True if the result set reached `cap`
      error:      "" on success, else a human-readable message
    """
    if not query or not query.strip():
        return [], False, "Enter a gauge name to search."
    tokens = _tokenize(query)
    if not tokens:
        return [], False, "Enter a gauge name to search."
    feats, capped, error = _fetch(_where(tokens), cap)
    if error:
        return [], False, error
    candidates = [m for m in (_to_match(f) for f in feats) if m]
    candidates.sort(key=lambda m: (
        -_score(f"{m['name']} {m['waterbody']} {m['state']}", tokens),
        len(m["name"]), m["name"]))
    return candidates, capped, ""
```

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_noaa_search.py -v`
Expected: PASS — all six tests green.

- [ ] **Step 5: Commit**

```bash
git add monitor/noaa_search.py tests/monitor/test_noaa_search.py
git commit -m "feat: search NOAA gauges by name via the water/riv_gauges ArcGIS service"
```

---

### Task 4: Paging cache → `monitor/search_cache.py`

**Files:**
- Create: `monitor/search_cache.py`
- Test: `tests/monitor/test_search_cache.py`

**Interfaces:**
- Produces: `get_or_compute(key, producer, ttl=120, clock=time.monotonic) -> value` — returns the cached value if present and unexpired, else calls `producer()`, stores, and returns it. `clear()` empties the cache.

- [ ] **Step 1: Write the failing tests**

Create `tests/monitor/test_search_cache.py`:

```python
from monitor.search_cache import get_or_compute, clear


def setup_function():
    clear()


def test_caches_within_ttl_producer_called_once():
    calls = {"n": 0}
    def produce():
        calls["n"] += 1
        return "v"
    assert get_or_compute("k", produce, ttl=100, clock=lambda: 0) == "v"
    assert get_or_compute("k", produce, ttl=100, clock=lambda: 50) == "v"
    assert calls["n"] == 1


def test_recomputes_after_expiry():
    seq = {"n": 0}
    def produce():
        seq["n"] += 1
        return seq["n"]
    assert get_or_compute("k", produce, ttl=10, clock=lambda: 0) == 1
    assert get_or_compute("k", produce, ttl=10, clock=lambda: 20) == 2  # expired


def test_distinct_keys_isolated():
    assert get_or_compute("a", lambda: 1, clock=lambda: 0) == 1
    assert get_or_compute("b", lambda: 2, clock=lambda: 0) == 2
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_search_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitor.search_cache'`.

- [ ] **Step 3: Create `monitor/search_cache.py`**

```python
"""Tiny in-process TTL cache so paginated searches reuse a ranked pool instead
of re-hitting the upstream APIs on every GET page request. Not shared across
processes — good enough for the single-container web server."""

import time

_STORE = {}  # key -> (expires_at, value)


def get_or_compute(key, producer, ttl=120, clock=time.monotonic):
    """Return the cached value for `key` if unexpired, else compute and store it."""
    now = clock()
    hit = _STORE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = producer()
    _STORE[key] = (now + ttl, value)
    return value


def clear():
    """Empty the cache (used by tests)."""
    _STORE.clear()
```

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_search_cache.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add monitor/search_cache.py tests/monitor/test_search_cache.py
git commit -m "feat: in-process TTL cache for paginated search pools"
```

---

### Task 5: `search_sites_by_name` returns the full ranked pool

**Files:**
- Modify: `monitor/site_search.py` (`search_sites_by_name` signature/returns)
- Modify: `web/routes.py` (`search_sites` unpacking — minimal adapter)
- Test: `tests/monitor/test_site_search.py`, `tests/web/test_sites.py`

**Interfaces:**
- Produces: `search_sites_by_name(query, cap=_FETCH_CAP) -> (candidates, capped, error)`. `candidates`: the FULL ranked pool (no `[:limit]` slice). `capped`: True if the candidate pool reached `cap`. All Step 1–3 relaxation/ranking logic unchanged.
- Consumed by: `web.routes.search_sites` (this task, adapter) and `combined_site_matches` (Task 6).

- [ ] **Step 1: Update the site_search unit tests to the new contract**

In `tests/monitor/test_site_search.py`, the orchestration tests currently unpack `(matches, truncated, error)`. Update them to `(candidates, capped, error)` and adjust assertions that depended on the `[:limit]` slice / `truncated` meaning:

```python
def test_clean_query_ranks_shortest_exact_name_first():
    long = _feature("03293500", "OHIO RIVER AT WATER TOWER AT LOUISVILLE, KY")
    short = _feature("03294500", "OHIO RIVER AT LOUISVILLE, KY")
    with patch("monitor.site_search._fetch", return_value=([long, short], "")):
        candidates, capped, error = search_sites_by_name("ohio river louisville")
    assert error == ""
    assert [m["number"] for m in candidates] == ["03294500", "03293500"]


def test_truncated_when_pool_reaches_cap():
    # A full pool (== cap) reports capped=True.
    feats = [_feature(str(i), f"MILL CREEK {i:02d}") for i in range(3)]
    with patch("monitor.site_search._fetch", return_value=(feats, "")), \
         patch("monitor.site_search._FETCH_CAP", 3):
        candidates, capped, error = search_sites_by_name("mill creek", cap=3)
    assert capped is True
    assert len(candidates) == 3
```

Update the other orchestration tests (`test_typo_query_relaxes_and_ranks_fuzzy_match_first`, `test_no_matches_returns_empty`, `test_api_error_propagates`, `test_disjoint_keywords_rank_rarest_keyword_first`) to unpack `(candidates, capped, error)` and assert on `candidates` (they already assert on membership/order, which is unchanged). Delete the old `test_truncated_when_more_candidates_than_limit`.

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_site_search.py -v`
Expected: FAIL — the updated tests expect the new `capped`/full-pool behavior the code doesn't have yet (e.g. `capped` is currently the `>limit` bool; full pool not returned).

- [ ] **Step 3: Change `search_sites_by_name` to return the full pool + `capped`**

In `monitor/site_search.py`, change the signature to `def search_sites_by_name(query, cap=_FETCH_CAP):` and update the docstring. Replace the two `return candidates[:limit], truncated, ""` sites (the Step-3 OR branch and the final return) so they return the full ranked pool and a `capped` flag:

- In the Step-3 OR branch (currently `truncated = len(candidates) > limit; return candidates[:limit], truncated, ""`):

```python
                candidates = [m for m, _groups in ranked]
                capped = len(candidates) >= cap
                return candidates, capped, ""
```

- In the final return (currently the last three lines):

```python
    candidates = [m for m in (_to_match(f) for f in features) if m]
    candidates.sort(key=lambda m: (-_score(m["name"], tokens),
                                   len(m["name"]), m["name"]))
    capped = len(candidates) >= cap
    return candidates, capped, ""
```

Also update the three early-return error paths (`return [], False, ...`) — they already return a falsy middle value, which now reads as `capped=False`; leave them as `return [], False, <msg>`.

- [ ] **Step 4: Adapt the `/sites` search route to the new contract (keep current behavior)**

In `web/routes.py`, in `search_sites` (still POST for now — Task 7 converts to GET), change the unpacking and the `truncated` passed to the template:

```python
        candidates, capped, error = search_sites_by_name(query)
        if error:
            flash(error, "danger")
            return redirect(url_for("sites"))
        if not candidates:
            flash(f"No gauges found matching {query!r}.", "warning")
            return redirect(url_for("sites"))

        matches = candidates[:25]
        try:
            annotate_liveness(matches)
        except Exception:
            logger.warning("Liveness enrichment failed", exc_info=True)
        try:
            annotate_noaa(matches)
        except Exception:
            logger.warning("NOAA enrichment failed", exc_info=True)
        matches.sort(key=lambda m: 0 if m.get("live") else 1)
        # ... existing DB read + render, but pass truncated=capped:
```

Update the `render_template("sites.html", ...)` call to pass `truncated=capped`.

- [ ] **Step 5: Update the two affected route tests**

In `tests/web/test_sites.py`, the `search_sites_by_name` mocks return a 3-tuple whose middle element is now `capped`. `_search_result` and `_enriched_search` already return `(matches, False, "")` — leave them. `test_search_truncated_shows_hint` mocks `(_search_result()[0], True, "")` (capped=True) and asserts `b"25 best matches"`; keep it — the route passes `truncated=capped=True`, so the hint still renders. No change needed unless an assertion referenced slicing; if `test_search_renders_liveness_and_noaa_badges` passed >25 mocked matches it would now slice — it passes 2, so unaffected. Verify by running.

- [ ] **Step 6: Run the affected suites**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_site_search.py tests/web/test_sites.py -v`
Expected: PASS — site_search returns full pool + capped; the Sites route renders unchanged (25 shown, hint on capped).

- [ ] **Step 7: Commit**

```bash
git add monitor/site_search.py web/routes.py tests/monitor/test_site_search.py tests/web/test_sites.py
git commit -m "refactor: search_sites_by_name returns full ranked pool + capped flag"
```

---

### Task 6: Combine USGS + NOAA for the Sites page → `monitor/gauge_discovery.py`

**Files:**
- Create: `monitor/gauge_discovery.py`
- Test: `tests/monitor/test_gauge_discovery.py`

**Interfaces:**
- Consumes: `search_sites_by_name` (Task 5), `search_noaa_gauges_by_name` (Task 3), `fetch_gauge_metadata` (Task 2, returns `usgs_id`).
- Produces: `combined_site_matches(query, max_workers=8) -> (rows, noaa_only_count, capped, error)`. `rows`: ranked list of addable USGS-site dicts `{number, name, state, site_type, noaa_name, noaa_lid}` (USGS-ranked first, then NOAA-discovered sites), deduped by site number. `noaa_only_count`: NOAA hits with no `usgs_id`. `error`: non-empty only if BOTH sources failed.

- [ ] **Step 1: Write the failing tests**

Create `tests/monitor/test_gauge_discovery.py`:

```python
from unittest.mock import patch
from monitor.gauge_discovery import combined_site_matches


def _usgs(*a, **k):
    return ([{"number": "03293551", "name": "OHIO R US OF MCALPINE DAM",
              "state": "Kentucky", "site_type": "Stream"}], False, "")


def _noaa(*a, **k):
    return ([{"lid": "MLUK2", "name": "McAlpine Upper",
              "waterbody": "Ohio River", "state": "KY"}], False, "")


def _meta(lid, timeout=10):
    return {"lid": "MLUK2", "station_name": "Ohio River at McAlpine Upper",
            "usgs_id": "03293551", "action_stage": 21.0,
            "minor_flood_stage": None, "moderate_flood_stage": None,
            "major_flood_stage": None}


def test_noaa_hit_dedups_onto_existing_usgs_row_with_tag():
    with patch("monitor.gauge_discovery.search_sites_by_name", side_effect=_usgs), \
         patch("monitor.gauge_discovery.search_noaa_gauges_by_name", side_effect=_noaa), \
         patch("monitor.gauge_discovery.fetch_gauge_metadata", side_effect=_meta):
        rows, noaa_only, capped, err = combined_site_matches("mcalpine upper")
    assert err == "" and noaa_only == 0
    assert len(rows) == 1                       # deduped by site number
    assert rows[0]["number"] == "03293551"
    assert rows[0]["noaa_name"] == "McAlpine Upper"   # tagged


def test_noaa_only_hit_with_usgsid_becomes_addable_row():
    def _usgs_empty(*a, **k):
        return ([], False, "")
    with patch("monitor.gauge_discovery.search_sites_by_name", side_effect=_usgs_empty), \
         patch("monitor.gauge_discovery.search_noaa_gauges_by_name", side_effect=_noaa), \
         patch("monitor.gauge_discovery.fetch_gauge_metadata", side_effect=_meta):
        rows, noaa_only, capped, err = combined_site_matches("mcalpine upper")
    assert [r["number"] for r in rows] == ["03293551"]
    assert rows[0]["noaa_name"] == "McAlpine Upper"


def test_noaa_hit_without_usgsid_is_counted_not_added():
    def _meta_no_usgs(lid, timeout=10):
        m = _meta(lid); m["usgs_id"] = None
        return m
    def _usgs_empty(*a, **k):
        return ([], False, "")
    with patch("monitor.gauge_discovery.search_sites_by_name", side_effect=_usgs_empty), \
         patch("monitor.gauge_discovery.search_noaa_gauges_by_name", side_effect=_noaa), \
         patch("monitor.gauge_discovery.fetch_gauge_metadata", side_effect=_meta_no_usgs):
        rows, noaa_only, capped, err = combined_site_matches("x")
    assert rows == [] and noaa_only == 1


def test_noaa_failure_still_returns_usgs_rows():
    def _noaa_fail(*a, **k):
        return ([], False, "NOAA search failed. Please try again later.")
    with patch("monitor.gauge_discovery.search_sites_by_name", side_effect=_usgs), \
         patch("monitor.gauge_discovery.search_noaa_gauges_by_name", side_effect=_noaa_fail):
        rows, noaa_only, capped, err = combined_site_matches("mcalpine")
    assert err == "" and [r["number"] for r in rows] == ["03293551"]


def test_both_sources_fail_surfaces_error():
    def _usgs_fail(*a, **k):
        return ([], False, "USGS search failed. Please try again later.")
    def _noaa_fail(*a, **k):
        return ([], False, "NOAA search failed. Please try again later.")
    with patch("monitor.gauge_discovery.search_sites_by_name", side_effect=_usgs_fail), \
         patch("monitor.gauge_discovery.search_noaa_gauges_by_name", side_effect=_noaa_fail):
        rows, noaa_only, capped, err = combined_site_matches("x")
    assert rows == [] and err != ""
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_gauge_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitor.gauge_discovery'`.

- [ ] **Step 3: Create `monitor/gauge_discovery.py`**

```python
"""
Combined USGS + NOAA gauge discovery for the Sites page.

Produces ONE ranked list of addable USGS-site rows: USGS name matches, plus
NOAA name matches resolved to their co-located USGS site via `usgs_id` (so a
gauge found by its NOAA name — e.g. "McAlpine Upper" — is addable as its USGS
site). A gauge found both ways is one row carrying the NOAA tag. NOAA gauges
with no USGS counterpart aren't addable here; they're counted for a UI note.
Best-effort: one source failing still returns the other's results.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from monitor.site_search import search_sites_by_name
from monitor.noaa_search import search_noaa_gauges_by_name
from monitor.noaa_client import fetch_gauge_metadata

logger = logging.getLogger(__name__)


def combined_site_matches(query, max_workers=8):
    """Merge USGS and NOAA name matches into one ranked list of USGS-site rows.

    Returns (rows, noaa_only_count, capped, error). `error` is non-empty only
    when BOTH sources fail.
    """
    usgs, usgs_capped, usgs_err = search_sites_by_name(query)
    noaa, noaa_capped, noaa_err = search_noaa_gauges_by_name(query)

    if usgs_err and noaa_err:
        return [], 0, False, usgs_err

    rows = {}
    for m in usgs:
        rows[m["number"]] = {**m, "noaa_name": None, "noaa_lid": None}

    def _resolve(hit):
        try:
            meta = fetch_gauge_metadata(hit["lid"])
        except Exception as e:
            logger.warning("NOAA->USGS resolve failed for %s: %s", hit["lid"], e)
            return None
        return (hit, meta)

    resolved = []
    if noaa:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            resolved = [r for r in pool.map(_resolve, noaa) if r]

    noaa_only = 0
    for hit, meta in resolved:
        usgs_id = meta.get("usgs_id") if meta else None
        if not usgs_id:
            noaa_only += 1
            continue
        existing = rows.get(usgs_id)
        if existing:
            existing["noaa_name"] = hit["name"]
            existing["noaa_lid"] = hit["lid"]
        else:
            rows[usgs_id] = {
                "number": usgs_id,
                "name": (meta.get("station_name") or hit["name"]),
                "state": hit.get("state", ""),
                "site_type": "",
                "noaa_name": hit["name"],
                "noaa_lid": hit["lid"],
            }

    return list(rows.values()), noaa_only, (usgs_capped or noaa_capped), ""
```

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_gauge_discovery.py -v`
Expected: PASS — all five tests green.

- [ ] **Step 5: Commit**

```bash
git add monitor/gauge_discovery.py tests/monitor/test_gauge_discovery.py
git commit -m "feat: combined USGS+NOAA site discovery, NOAA hits resolved to USGS sites"
```

---

### Task 7: Paginated GET Sites search + template

**Files:**
- Modify: `web/routes.py` (`search_sites` → GET, paginated, uses combined pool + cache)
- Modify: `web/templates/sites.html` (GET form, NOAA tag, pager, notes)
- Test: `tests/web/test_sites.py`

**Interfaces:**
- Consumes: `combined_site_matches` (Task 6), `search_cache.get_or_compute` (Task 4), `annotate_liveness`/`annotate_noaa`.
- Produces: `GET /sites/search?q=…&page=N` renders a paginated, source-labeled results table.

- [ ] **Step 1: Write the failing tests**

Replace the existing search route tests in `tests/web/test_sites.py` (they POST) with GET + pagination tests. Keep `_identity`. Add:

```python
def _rows(n):
    return [{"number": f"{i:08d}", "name": f"CREEK {i}", "state": "KY",
             "site_type": "Stream", "noaa_name": None, "noaa_lid": None}
            for i in range(n)]


def test_get_search_page1_lists_first_25_with_pager(client):
    pool = (_rows(30), 0, False, "")
    with patch("web.routes.combined_site_matches", return_value=pool), \
         patch("web.routes.annotate_liveness", side_effect=_identity), \
         patch("web.routes.annotate_noaa", side_effect=_identity):
        resp = client.get("/sites/search?q=creek&page=1")
    body = resp.data.decode()
    assert resp.status_code == 200
    assert "CREEK 0" in body and "CREEK 24" in body
    assert "CREEK 25" not in body            # page 2 content
    assert "Page 1 of 2" in body
    assert "30 matches" in body


def test_get_search_page2_shows_next_rows(client):
    pool = (_rows(30), 0, False, "")
    with patch("web.routes.combined_site_matches", return_value=pool), \
         patch("web.routes.annotate_liveness", side_effect=_identity), \
         patch("web.routes.annotate_noaa", side_effect=_identity):
        resp = client.get("/sites/search?q=creek&page=2")
    body = resp.data.decode()
    assert "CREEK 25" in body and "CREEK 29" in body
    assert "Page 2 of 2" in body


def test_get_search_renders_noaa_tag(client):
    rows = [{"number": "03293551", "name": "OHIO R US OF MCALPINE DAM",
             "state": "KY", "site_type": "Stream",
             "noaa_name": "McAlpine Upper", "noaa_lid": "MLUK2"}]
    with patch("web.routes.combined_site_matches", return_value=(rows, 2, False, "")), \
         patch("web.routes.annotate_liveness", side_effect=_identity), \
         patch("web.routes.annotate_noaa", side_effect=_identity):
        resp = client.get("/sites/search?q=mcalpine+upper")
    body = resp.data.decode()
    assert "McAlpine Upper" in body                  # NOAA tag
    assert "2 NOAA-only" in body                     # noaa_only note


def test_get_search_no_matches_flashes(client):
    with patch("web.routes.combined_site_matches", return_value=([], 0, False, "")):
        resp = client.get("/sites/search?q=zzzz", follow_redirects=True)
    assert b"No gauges found" in resp.data


def test_get_search_error_flashes(client):
    with patch("web.routes.combined_site_matches",
               return_value=([], 0, False, "USGS search failed. Please try again later.")):
        resp = client.get("/sites/search?q=x", follow_redirects=True)
    assert b"USGS search failed" in resp.data
```

Remove the now-obsolete POST-era tests (`test_search_renders_matches_dropdown`, `test_search_no_matches_flashes`, `test_search_api_error_flashes`, `test_search_empty_query_flashes`, `test_search_truncated_shows_hint`, `test_search_renders_liveness_and_noaa_badges`, `test_search_survives_enrichment_failure`) — their behaviors are re-covered above and by the liveness/badge assertions retained below. Keep a badge test adapted to GET:

```python
def test_get_search_renders_liveness_badges(client):
    rows = [{"number": "03294500", "name": "OHIO RIVER AT LOUISVILLE, KY",
             "state": "KY", "site_type": "Stream", "noaa_name": None, "noaa_lid": None}]
    def _live(ms, *a, **k):
        for m in ms:
            m.update(live=True, last_value=63000.0, last_time="t")
        return ms
    with patch("web.routes.combined_site_matches", return_value=(rows, 0, False, "")), \
         patch("web.routes.annotate_liveness", side_effect=_live), \
         patch("web.routes.annotate_noaa", side_effect=_identity):
        resp = client.get("/sites/search?q=ohio")
    assert b"Reporting" in resp.data
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_sites.py -v`
Expected: FAIL — `combined_site_matches` not imported in `web.routes`; `/sites/search` still POST-only (GET → 405).

- [ ] **Step 3: Add the import**

In `web/routes.py`, after the existing `from monitor.gauge_enrich import ...` line add:

```python
from monitor.gauge_discovery import combined_site_matches
from monitor.search_cache import get_or_compute
```

- [ ] **Step 4: Replace `search_sites` with a paginated GET route**

Replace the entire `search_sites` function in `web/routes.py` with:

```python
    @app.route("/sites/search", methods=["GET"])
    def search_sites():
        """GET /sites/search?q=&page= — paginated USGS+NOAA gauge search."""
        db_path = current_app.config["DB_PATH"]
        query = request.args.get("q", "").strip()
        if not query:
            flash("Enter a gauge name to search.", "danger")
            return redirect(url_for("sites"))
        page = request.args.get("page", 1, type=int)
        if page < 1:
            page = 1

        rows, noaa_only, capped, error = get_or_compute(
            f"sites:{query.lower()}", lambda: combined_site_matches(query))
        if error:
            flash(error, "danger")
            return redirect(url_for("sites"))
        if not rows:
            flash(f"No gauges found matching {query!r}.", "warning")
            return redirect(url_for("sites"))

        per_page = 25
        total = len(rows)
        pages = (total + per_page - 1) // per_page
        page = min(page, pages)
        start = (page - 1) * per_page
        page_rows = rows[start:start + per_page]

        try:
            annotate_liveness(page_rows)
        except Exception:
            logger.warning("Liveness enrichment failed", exc_info=True)
        try:
            annotate_noaa(page_rows)
        except Exception:
            logger.warning("NOAA enrichment failed", exc_info=True)
        page_rows.sort(key=lambda m: 0 if m.get("live") else 1)

        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute("SELECT * FROM sites ORDER BY station_name")
        all_sites = cur.fetchall()
        cur.close()
        conn.close()
        return render_template(
            "sites.html", sites=all_sites, matches=page_rows, query=query,
            page=page, pages=pages, total=total, capped=capped,
            noaa_only=noaa_only)
```

- [ ] **Step 5: Update `sites.html` — GET form, NOAA tag, pager, notes**

In `web/templates/sites.html`: change the search form to `method="get"` with `name="q"`:

```html
<form method="get" action="/sites/search" class="row g-2 mb-1">
  <div class="col-md-8">
    <input name="q" class="form-control"
           placeholder="Keywords in any order (e.g. louisville ohio river)"
           value="{{ query or '' }}" required>
  </div>
  <div class="col-md-2"><button class="btn btn-secondary w-100">Search</button></div>
</form>
```

In the matches table's Station cell, add the NOAA tag when present:

```html
        <td>{{ m.name }}{% if m.state %} <span class="text-muted small">— {{ m.state }}</span>{% endif %}
          {% if m.noaa_name %}<span class="badge bg-info text-dark ms-1" title="Matched NOAA gauge {{ m.noaa_lid }}">matched NOAA: {{ m.noaa_name }}</span>{% endif %}
        </td>
```

Replace the old truncation hint block (`{% if truncated %}…`) with a pager + notes (inside the `{% if matches %}` block, after the table):

```html
  {% if pages > 1 %}
  <nav class="d-flex justify-content-between align-items-center">
    <span class="text-muted small">Page {{ page }} of {{ pages }} · {{ total }} matches</span>
    <div class="btn-group">
      {% if page > 1 %}<a class="btn btn-sm btn-outline-secondary" href="{{ url_for('search_sites', q=query, page=page-1) }}">← Prev</a>{% endif %}
      {% if page < pages %}<a class="btn btn-sm btn-outline-secondary" href="{{ url_for('search_sites', q=query, page=page+1) }}">Next →</a>{% endif %}
    </div>
  </nav>
  {% else %}
  <span class="text-muted small">{{ total }} match{{ '' if total == 1 else 'es' }}</span>
  {% endif %}
  {% if capped %}<div><small class="text-muted">Showing the best {{ total }} — add more keywords to narrow it down.</small></div>{% endif %}
  {% if noaa_only %}<div><small class="text-muted">+ {{ noaa_only }} NOAA-only gauge{{ '' if noaa_only == 1 else 's' }} matched — add those via a landing page.</small></div>{% endif %}
```

(The per-row Add form and parameter picker from the prior design stay as-is.)

- [ ] **Step 6: Run the sites route tests**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_sites.py -v`
Expected: PASS — GET pagination, NOAA tag, notes, badges all render; obsolete POST tests removed.

- [ ] **Step 7: Commit**

```bash
git add web/routes.py web/templates/sites.html tests/web/test_sites.py
git commit -m "feat: paginated GET Sites search over combined USGS+NOAA results"
```

---

### Task 8: Landing-page editor — direct NOAA search, paginated GET

**Files:**
- Modify: `web/routes.py` (`page_search_gauges` → GET, direct NOAA search, paginated)
- Modify: `web/templates/page_edit.html` (GET form, results, pager)
- Test: `tests/web/test_pages.py`

**Interfaces:**
- Consumes: `search_noaa_gauges_by_name` (Task 3), `search_cache.get_or_compute` (Task 4).
- Produces: `GET /edit/<edit_token>/gauges/search?q=…&page=N` lists NOAA gauges by their NOAA names, paginated; add-by-LID (`page_add_gauge`) unchanged.

- [ ] **Step 1: Write the failing tests**

In `tests/web/test_pages.py`, replace the Task-5-era `page_search_gauges` tests. Add:

```python
def _noaa_pool(*a, **k):
    cands = [{"lid": "MLUK2", "name": "McAlpine Upper",
              "waterbody": "Ohio River", "state": "KY"},
             {"lid": "MLPK2", "name": "McAlpine Lower",
              "waterbody": "Ohio River", "state": "KY"}]
    return cands, False, ""


def test_editor_noaa_search_lists_gauges_by_noaa_name(client):
    edit_token = _make_edit_token(client)
    with patch("web.routes.search_noaa_gauges_by_name", side_effect=_noaa_pool):
        resp = client.get(f"/edit/{edit_token}/gauges/search?q=mcalpine+upper")
    body = resp.data.decode()
    assert "McAlpine Upper" in body and "MLUK2" in body


def test_editor_noaa_search_paginates(client):
    edit_token = _make_edit_token(client)
    many = ([{"lid": f"L{i}", "name": f"GAUGE {i}", "waterbody": "R", "state": "KY"}
             for i in range(30)], False, "")
    with patch("web.routes.search_noaa_gauges_by_name", return_value=many):
        resp = client.get(f"/edit/{edit_token}/gauges/search?q=gauge&page=2")
    body = resp.data.decode()
    assert "GAUGE 25" in body and "GAUGE 29" in body
    assert "Page 2 of 2" in body


def test_editor_noaa_search_survives_failure(client):
    edit_token = _make_edit_token(client)
    with patch("web.routes.search_noaa_gauges_by_name",
               return_value=([], False, "NOAA search failed. Please try again later.")):
        resp = client.get(f"/edit/{edit_token}/gauges/search?q=x",
                          follow_redirects=True)
    assert resp.status_code == 200
    assert b"NOAA search failed" in resp.data
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_pages.py -k editor_noaa -v`
Expected: FAIL — route still POST + uses `search_sites_by_name`/`annotate_noaa`; `search_noaa_gauges_by_name` not imported in `web.routes`.

- [ ] **Step 3: Import the NOAA search in routes**

In `web/routes.py`, add near the other monitor imports:

```python
from monitor.noaa_search import search_noaa_gauges_by_name
```

- [ ] **Step 4: Replace `page_search_gauges` with a paginated GET route**

Replace the `page_search_gauges` function with:

```python
    @app.route("/edit/<edit_token>/gauges/search", methods=["GET"])
    def page_search_gauges(edit_token):
        """GET /edit/<edit_token>/gauges/search?q=&page= — find NOAA gauges by name."""
        from flask import abort
        db_path = current_app.config["DB_PATH"]
        from db.models import (get_page_by_edit_token, get_page_gauges,
                               get_active_page_subscribers)
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)

        query = request.args.get("q", "").strip()
        pageno = request.args.get("page", 1, type=int)
        if pageno < 1:
            pageno = 1
        gauge_matches, pages, total = [], 1, 0
        if not query:
            flash("Enter a gauge name to search.", "danger")
        else:
            cands, _capped, error = get_or_compute(
                f"noaa:{query.lower()}", lambda: search_noaa_gauges_by_name(query))
            if error:
                flash(error, "danger")
            elif not cands:
                flash(f"No NOAA gauges found matching {query!r}.", "warning")
            else:
                per_page = 25
                total = len(cands)
                pages = (total + per_page - 1) // per_page
                pageno = min(pageno, pages)
                start = (pageno - 1) * per_page
                gauge_matches = cands[start:start + per_page]

        gauges = get_page_gauges(page["id"], db_path)
        subscribers = get_active_page_subscribers(page["id"], db_path)
        return render_template(
            "page_edit.html", page=page, gauges=gauges, subscribers=subscribers,
            edit_token=edit_token, gauge_matches=gauge_matches, gauge_query=query,
            gauge_page=pageno, gauge_pages=pages, gauge_total=total)
```

- [ ] **Step 5: Update `page_edit.html` — GET form, results, pager**

Change the gauge search form to `method="get"` with `name="q"`:

```html
        <form method="get" action="{{ url_for('page_search_gauges', edit_token=edit_token) }}" class="input-group mb-2">
          <input type="text" name="q" class="form-control"
                 placeholder="Search NOAA gauges by name (e.g. mcalpine upper)"
                 value="{{ gauge_query or '' }}" required>
          <button class="btn btn-secondary" type="submit">Search</button>
        </form>
```

The results list uses `m.lid`/`m.name` (NOAA candidates now, not USGS-bridged). Update the add-form hidden field and the label:

```html
        {% if gauge_matches %}
        <ul class="list-group mb-2">
          {% for m in gauge_matches %}
          <li class="list-group-item d-flex justify-content-between align-items-center">
            <span>{{ m.name }} <span class="text-muted small">{{ m.waterbody }} ({{ m.lid }})</span></span>
            <form method="post" action="{{ url_for('page_add_gauge', edit_token=edit_token) }}" class="m-0">
              <input type="hidden" name="lid" value="{{ m.lid }}">
              <button class="btn btn-sm btn-primary">Add</button>
            </form>
          </li>
          {% endfor %}
        </ul>
        {% if gauge_pages > 1 %}
        <nav class="d-flex justify-content-between align-items-center mb-2">
          <span class="text-muted small">Page {{ gauge_page }} of {{ gauge_pages }} · {{ gauge_total }} matches</span>
          <div class="btn-group">
            {% if gauge_page > 1 %}<a class="btn btn-sm btn-outline-secondary" href="{{ url_for('page_search_gauges', edit_token=edit_token, q=gauge_query, page=gauge_page-1) }}">← Prev</a>{% endif %}
            {% if gauge_page < gauge_pages %}<a class="btn btn-sm btn-outline-secondary" href="{{ url_for('page_search_gauges', edit_token=edit_token, q=gauge_query, page=gauge_page+1) }}">Next →</a>{% endif %}
          </div>
        </nav>
        {% endif %}
        {% endif %}
```

Keep the manual "Add by ID" fallback form below (unchanged).

- [ ] **Step 6: Run the page tests**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_pages.py -v`
Expected: PASS — editor NOAA search + pagination render; `page_add_gauge` tests still pass (add-by-LID unchanged).

- [ ] **Step 7: Commit**

```bash
git add web/routes.py web/templates/page_edit.html tests/web/test_pages.py
git commit -m "feat: landing-page editor searches NOAA names directly, paginated GET"
```

---

### Task 9: Full suite + live-API smoke checks

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest -q`
Expected: PASS — all tests green, output pristine (no stray warnings).

- [ ] **Step 2: Live-check NOAA name search**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test python -c "from monitor.noaa_search import search_noaa_gauges_by_name; c,cap,e=search_noaa_gauges_by_name('mcalpine upper'); print(e, [x['lid'] for x in c][:5])"`
Expected: prints an empty error and a list containing `MLUK2` — NOAA name search finds "McAlpine Upper" directly.

- [ ] **Step 3: Live-check the combined Sites pool**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test python -c "from monitor.gauge_discovery import combined_site_matches; r,n,cap,e=combined_site_matches('mcalpine upper'); print(e, n, [(x['number'], x.get('noaa_name')) for x in r if x['number']=='03293551'])"`
Expected: shows `03293551` present with `noaa_name` `McAlpine Upper` — the McAlpine gauge is findable and NOAA-tagged.

- [ ] **Step 4: Commit (empty) to mark verification**

```bash
git commit --allow-empty -m "chore: NOAA search + pagination verified end-to-end"
```

---

## Self-Review

**Spec coverage:**
- NOAA names searchable → Task 3 (`search_noaa_gauges_by_name`) + Task 8 (editor) + Task 6/7 (Sites). ✓
- `fetch_gauge_metadata` returns `usgs_id` → Task 2. ✓
- Shared ranking helpers → Task 1. ✓
- Pagination (ranked, capped, 25/page, GET, cached) → Task 4 (cache) + Task 5 (full pool) + Tasks 7/8 (routes). ✓
- Sites page: combined, source-labeled, NOAA→USGS resolution, dedup, noaa-only note → Task 6 + Task 7. ✓
- Editor: direct NOAA search → Task 8. ✓
- Best-effort/degradation → per-source failure handling in Tasks 3/6/7/8 with explicit tests. ✓
- Out of scope respected (no merged rewrite, no unbounded paging, no persistent NOAA cache). ✓

**Placeholder scan:** none — every code/test step is complete. The `_STATE_ABBR` move (Task 1 Step 3) reproduces the dict verbatim.

**Type consistency:** `search_sites_by_name` new contract `(candidates, capped, error)` (Task 5) is consumed by `combined_site_matches` (Task 6) and the Sites route (Task 7). `search_noaa_gauges_by_name` `(candidates, capped, error)` with `{lid,name,waterbody,state}` (Task 3) is consumed by Task 6 and Task 8. `fetch_gauge_metadata`'s `usgs_id` (Task 2) is read in Task 6. `combined_site_matches` `(rows, noaa_only_count, capped, error)` with row keys `{number,name,state,site_type,noaa_name,noaa_lid}` (Task 6) is consumed and rendered in Task 7. `get_or_compute(key, producer, ttl, clock)` (Task 4) is called in Tasks 7/8. Template variable names (`page/pages/total/capped/noaa_only`, `gauge_page/gauge_pages/gauge_total`) match between routes and templates. ✓
