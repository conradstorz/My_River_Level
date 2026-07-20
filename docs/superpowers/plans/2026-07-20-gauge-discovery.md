# Reliable, Unified Gauge Discovery (USGS + NOAA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make USGS and NOAA gauges reliably discoverable — flag dead USGS gauges in search results and let users find NOAA gauges by name instead of typing an LID.

**Architecture:** Build on the existing ranked USGS name search (`monitor/site_search.py`). Add a best-effort enrichment layer (`monitor/gauge_enrich.py`) that annotates each shown result with USGS liveness (one batch `nwis.get_iv` call) and NOAA availability (concurrent `GET /nwps/v1/gauges/{usgs#}` lookups, reusing the fact that the NWPS endpoint accepts a USGS site number). The same enriched search feeds both the Sites page and the landing-page editor. The two alert subsystems stay distinct; only discovery is unified.

**Tech Stack:** Python 3, Flask, Jinja2, `requests`, `dataretrieval` (`nwis`), pandas, PostgreSQL (psycopg2), pytest.

## Global Constraints

- Never chain shell commands (`&&`, `||`, `|`, `;`). One command per Bash call.
- Tests run with `pytest` against `TEST_DATABASE_URL`; the `tmp_db` fixture drops/recreates tables per test.
- Enrichment is strictly additive and best-effort: any failure in liveness or NOAA lookup must leave the search working, never raise to the user.
- Reuse the existing ranking in `monitor/site_search.py`; do not duplicate or reorder its relevance logic (liveness only re-groups live-before-dead, stably).
- No new third-party data providers. No persistent NOAA gauge-list cache. No merging of the flow-percentile and flood-category alert subsystems.
- Follow existing test patterns: mock `nwis.*` and `requests.get` at the module where they are used.

---

### Task 1: NOAA client — resolve LID, accept USGS number, parse both category shapes

**Files:**
- Modify: `monitor/noaa_client.py:33-73` (`fetch_gauge_metadata`)
- Test: `tests/monitor/test_noaa_client.py`

**Interfaces:**
- Produces: `fetch_gauge_metadata(identifier, timeout=10) -> dict | None`. `identifier` may be a NOAA LID **or** a USGS site number (the NWPS endpoint resolves both). Return dict keys: `station_name` (str), `lid` (str, the canonical NOAA LID from the API), `action_stage`, `minor_flood_stage`, `moderate_flood_stage`, `major_flood_stage` (float or None). Returns `None` on HTTP error / exception.

- [ ] **Step 1: Write the failing test for dict-shaped categories + lid**

Add to `tests/monitor/test_noaa_client.py`:

```python
def _mock_metadata_dict_response():
    """Real NWPS shape: flood.categories is a dict keyed by name, plus a lid."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "lid": "MLUK2",
        "usgsId": "03293551",
        "name": "Ohio River at McAlpine Upper",
        "flood": {
            "categories": {
                "action":   {"stage": 21.0, "flow": 484486},
                "minor":    {"stage": 23.0, "flow": 512700},
                "moderate": {"stage": 30.0, "flow": 630001},
                "major":    {"stage": 38.0, "flow": 783550},
            }
        },
    }
    return mock


def test_fetch_gauge_metadata_dict_categories_and_lid():
    with patch("monitor.noaa_client.requests.get",
               return_value=_mock_metadata_dict_response()):
        meta = fetch_gauge_metadata("03293551")  # looked up by USGS number
    assert meta["lid"] == "MLUK2"
    assert meta["station_name"] == "Ohio River at McAlpine Upper"
    assert meta["action_stage"] == 21.0
    assert meta["minor_flood_stage"] == 23.0
    assert meta["moderate_flood_stage"] == 30.0
    assert meta["major_flood_stage"] == 38.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/monitor/test_noaa_client.py::test_fetch_gauge_metadata_dict_categories_and_lid -v`
Expected: FAIL — `meta["lid"]` KeyError (function doesn't return `lid` yet) or thresholds are None (dict shape not parsed).

- [ ] **Step 3: Rewrite `fetch_gauge_metadata` to add `lid`, accept an identifier, parse both shapes**

Replace `monitor/noaa_client.py` lines 33-73 (the whole `fetch_gauge_metadata` function) with:

```python
def fetch_gauge_metadata(identifier, timeout=TIMEOUT):
    """
    Fetch station name, canonical LID, and flood thresholds from NWPS.

    `identifier` may be a NOAA LID or a USGS site number — the NWPS
    /gauges/{id} endpoint resolves both.

    NWPS returns flood categories either as a dict keyed by category name
    ({"action": {"stage": 21.0}, ...}) or as a list of {"name", "stage"}.
    Both shapes are handled.

    Returns a dict with keys:
        station_name, lid, action_stage, minor_flood_stage,
        moderate_flood_stage, major_flood_stage
    or None on error.
    """
    url = f"{NWPS_BASE}/gauges/{identifier.lower()}"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("NOAA metadata fetch failed for %s: HTTP %s",
                           identifier, resp.status_code)
            return None
        data = resp.json()
    except Exception:
        logger.exception("Error fetching NOAA metadata for %s", identifier)
        return None

    thresholds = {"action_stage": None, "minor_flood_stage": None,
                  "moderate_flood_stage": None, "major_flood_stage": None}
    key_map = {
        "action":   "action_stage",
        "minor":    "minor_flood_stage",
        "moderate": "moderate_flood_stage",
        "major":    "major_flood_stage",
    }
    raw = (data.get("flood") or {}).get("categories")
    if isinstance(raw, dict):
        items = [{"name": k, "stage": (v or {}).get("stage")}
                 for k, v in raw.items()]
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    for cat in items:
        name = (cat.get("name") or "").lower()
        if name in key_map:
            thresholds[key_map[name]] = cat.get("stage")

    return {
        "station_name": data.get("name", identifier),
        "lid": data.get("lid", identifier),
        **thresholds,
    }
```

- [ ] **Step 4: Run the NOAA client tests to verify all pass (new + existing list-shape)**

Run: `pytest tests/monitor/test_noaa_client.py -v`
Expected: PASS — the new dict-shape test passes, and the existing `test_fetch_gauge_metadata` (list shape) and `test_fetch_gauge_metadata_http_error` still pass.

- [ ] **Step 5: Commit**

```bash
git add monitor/noaa_client.py tests/monitor/test_noaa_client.py
git commit -m "feat: NOAA metadata resolves LID, accepts USGS number, parses both category shapes"
```

---

### Task 2: Liveness enrichment (`annotate_liveness`)

**Files:**
- Create: `monitor/gauge_enrich.py`
- Test: `tests/monitor/test_gauge_enrich.py`

**Interfaces:**
- Consumes: match dicts from `site_search.search_sites_by_name` — each has `number`, `name`, `state`, `site_type`.
- Produces: `annotate_liveness(matches, param_code="00060") -> list[dict]`. Mutates and returns the same list. Adds to each match: `live` (bool, default False), `last_value` (float or None), `last_time` (str or None). Best-effort: on any failure the matches are returned unchanged (all `live=False`).

- [ ] **Step 1: Write the failing tests**

Create `tests/monitor/test_gauge_enrich.py`:

```python
"""Tests for gauge search enrichment (liveness + NOAA availability)."""

from unittest.mock import patch

import pandas as pd

from monitor.gauge_enrich import annotate_liveness


def _matches():
    return [
        {"number": "03294500", "name": "OHIO RIVER AT LOUISVILLE, KY"},
        {"number": "09999999", "name": "DEAD GAUGE"},
    ]


def _iv_frame():
    # Shape after nwis.get_iv(sites=[...]) is normalized with reset_index():
    # a site_no column, a datetime column, and a value column named by param.
    return pd.DataFrame({
        "site_no": ["03294500", "03294500"],
        "datetime": pd.to_datetime(["2026-07-19T10:00:00Z",
                                    "2026-07-19T10:15:00Z"]),
        "00060": [1000.0, 1010.0],
    })


def test_annotate_liveness_flags_live_and_dead():
    with patch("monitor.gauge_enrich.nwis.get_iv",
               return_value=(_iv_frame(), {})):
        out = annotate_liveness(_matches())
    live = {m["number"]: m for m in out}
    assert live["03294500"]["live"] is True
    assert live["03294500"]["last_value"] == 1010.0
    assert live["03294500"]["last_time"] is not None
    # A site with no rows in the window is not live.
    assert live["09999999"]["live"] is False


def test_annotate_liveness_survives_api_failure():
    with patch("monitor.gauge_enrich.nwis.get_iv",
               side_effect=Exception("USGS down")):
        out = annotate_liveness(_matches())
    assert all(m["live"] is False for m in out)   # unchanged, no raise
    assert len(out) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/monitor/test_gauge_enrich.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitor.gauge_enrich'`.

- [ ] **Step 3: Create `monitor/gauge_enrich.py` with `annotate_liveness`**

```python
"""
Best-effort enrichment of gauge search results.

Layered on top of the ranked USGS name search (monitor/site_search.py) so the
UI can flag which gauges still report live data (annotate_liveness) and which
have a co-located NOAA flood-forecast point (annotate_noaa, added later).

Every function here is best-effort: on any API failure it returns the matches
unchanged rather than raising, so enrichment can never break search.
"""

import logging
from datetime import datetime, timedelta

import pandas as pd
import dataretrieval.nwis as nwis

logger = logging.getLogger(__name__)

LIVE_WINDOW_DAYS = 5  # a reading within this window counts as "reporting"


def annotate_liveness(matches, param_code="00060"):
    """Flag which matches have a recent USGS reading (one batch get_iv call).

    Adds `live` (bool), `last_value` (float|None), `last_time` (str|None) to
    each match. Best-effort — returns matches unchanged on any failure.
    """
    for m in matches:
        m.setdefault("live", False)
        m.setdefault("last_value", None)
        m.setdefault("last_time", None)

    numbers = [m["number"] for m in matches if m.get("number")]
    if not numbers:
        return matches

    end = datetime.now()
    start = end - timedelta(days=LIVE_WINDOW_DAYS)
    try:
        df, _ = nwis.get_iv(
            sites=numbers,
            parameterCd=param_code,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
    except Exception as e:
        logger.warning("Liveness batch fetch failed: %s", e)
        return matches

    if df is None or len(df) == 0:
        return matches

    # Normalize: get_iv may return a MultiIndex (site_no, datetime) or a
    # datetime index; reset_index turns both into plain columns.
    try:
        df = df.reset_index()
    except Exception:
        return matches

    if "site_no" not in df.columns:
        return matches
    param_cols = [c for c in df.columns if str(c).startswith(param_code)]
    if not param_cols:
        return matches
    pcol = param_cols[0]
    timecol = "datetime" if "datetime" in df.columns else None

    for m in matches:
        rows = df[df["site_no"] == m["number"]]
        if len(rows) == 0:
            continue
        last = rows.iloc[-1]
        val = pd.to_numeric(last[pcol], errors="coerce")
        if pd.isna(val):
            continue
        m["live"] = True
        m["last_value"] = float(val)
        m["last_time"] = str(last[timecol]) if timecol else None

    return matches
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/monitor/test_gauge_enrich.py -v`
Expected: PASS — both liveness tests green.

- [ ] **Step 5: Commit**

```bash
git add monitor/gauge_enrich.py tests/monitor/test_gauge_enrich.py
git commit -m "feat: annotate_liveness flags USGS gauges with recent data"
```

---

### Task 3: NOAA-availability enrichment (`annotate_noaa`)

**Files:**
- Modify: `monitor/gauge_enrich.py`
- Test: `tests/monitor/test_gauge_enrich.py`

**Interfaces:**
- Consumes: `fetch_gauge_metadata(identifier, timeout=...)` from Task 1.
- Produces: `annotate_noaa(matches, timeout=6, max_workers=8) -> list[dict]`. Mutates and returns the same list. Adds to each match: `noaa_lid` (str or None), `noaa_has_flood` (bool). A match with a co-located NWPS gauge that has at least one flood threshold gets `noaa_has_flood=True` and its canonical `noaa_lid`. Best-effort and concurrent; per-item failures leave that match's NOAA fields empty.

- [ ] **Step 1: Write the failing tests**

Add to `tests/monitor/test_gauge_enrich.py`:

```python
from monitor.gauge_enrich import annotate_noaa


def _fake_noaa(identifier, timeout=6):
    if identifier == "03293551":
        return {"lid": "MLUK2", "station_name": "Ohio River",
                "action_stage": 21.0, "minor_flood_stage": 23.0,
                "moderate_flood_stage": 30.0, "major_flood_stage": 38.0}
    return None  # no NOAA gauge at this USGS site (HTTP 404)


def test_annotate_noaa_flags_forecast_gauges():
    matches = [
        {"number": "03293551", "name": "OHIO RIVER"},
        {"number": "09999999", "name": "NO NOAA HERE"},
    ]
    with patch("monitor.gauge_enrich.fetch_gauge_metadata",
               side_effect=_fake_noaa):
        out = annotate_noaa(matches)
    by = {m["number"]: m for m in out}
    assert by["03293551"]["noaa_has_flood"] is True
    assert by["03293551"]["noaa_lid"] == "MLUK2"
    assert by["09999999"]["noaa_has_flood"] is False
    assert by["09999999"]["noaa_lid"] is None


def test_annotate_noaa_survives_lookup_failure():
    matches = [{"number": "03293551", "name": "OHIO RIVER"}]
    with patch("monitor.gauge_enrich.fetch_gauge_metadata",
               side_effect=Exception("NWPS down")):
        out = annotate_noaa(matches)
    assert out[0]["noaa_has_flood"] is False   # no raise
    assert out[0]["noaa_lid"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/monitor/test_gauge_enrich.py -k annotate_noaa -v`
Expected: FAIL — `ImportError: cannot import name 'annotate_noaa'`.

- [ ] **Step 3: Add `annotate_noaa` to `monitor/gauge_enrich.py`**

Add the import near the top (below the existing imports):

```python
from concurrent.futures import ThreadPoolExecutor

from monitor.noaa_client import fetch_gauge_metadata
```

Append this function to `monitor/gauge_enrich.py`:

```python
def annotate_noaa(matches, timeout=6, max_workers=8):
    """Flag which matches have a co-located NOAA flood-forecast gauge.

    The NWPS /gauges/{id} endpoint accepts a USGS site number, so each USGS
    match can be probed directly. Adds `noaa_lid` (str|None) and
    `noaa_has_flood` (bool). Concurrent and best-effort — a failed lookup
    leaves that match's NOAA fields empty.
    """
    for m in matches:
        m.setdefault("noaa_lid", None)
        m.setdefault("noaa_has_flood", False)

    def _probe(m):
        number = m.get("number")
        if not number:
            return
        try:
            meta = fetch_gauge_metadata(number, timeout=timeout)
        except Exception as e:
            logger.warning("NOAA probe failed for %s: %s", number, e)
            return
        if not meta:
            return
        has_flood = any(meta.get(k) is not None for k in (
            "action_stage", "minor_flood_stage",
            "moderate_flood_stage", "major_flood_stage"))
        m["noaa_lid"] = meta.get("lid")
        m["noaa_has_flood"] = bool(has_flood)

    if matches:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(_probe, matches))
    return matches
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/monitor/test_gauge_enrich.py -v`
Expected: PASS — all four enrichment tests green.

- [ ] **Step 5: Commit**

```bash
git add monitor/gauge_enrich.py tests/monitor/test_gauge_enrich.py
git commit -m "feat: annotate_noaa flags gauges with a co-located NOAA flood forecast"
```

---

### Task 4: Wire enrichment into `/sites/search` + results table

**Files:**
- Modify: `web/routes.py:14-17` (imports), `web/routes.py:308-342` (`search_sites` route)
- Modify: `web/templates/sites.html:17-38` (matches block)
- Test: `tests/web/test_sites.py`

**Interfaces:**
- Consumes: `annotate_liveness`, `annotate_noaa` from `monitor.gauge_enrich`.
- Produces: `/sites/search` renders enriched, live-sorted matches with liveness and NOAA badges; each match row is its own add form.

- [ ] **Step 1: Write the failing test**

Add to `tests/web/test_sites.py`:

```python
def _enriched_search(*args, **kwargs):
    matches = [
        {"number": "03293551", "name": "OHIO RIVER AT MCALPINE",
         "state": "Kentucky", "site_type": "Stream"},
        {"number": "09999999", "name": "DEAD CREEK",
         "state": "Kentucky", "site_type": "Stream"},
    ]
    return matches, False, ""


def _add_liveness(matches, *a, **k):
    for m in matches:
        if m["number"] == "03293551":
            m.update(live=True, last_value=12.8, last_time="2026-07-19T10:15")
        else:
            m.update(live=False, last_value=None, last_time=None)
    return matches


def _add_noaa(matches, *a, **k):
    for m in matches:
        if m["number"] == "03293551":
            m.update(noaa_lid="MLUK2", noaa_has_flood=True)
        else:
            m.update(noaa_lid=None, noaa_has_flood=False)
    return matches


def test_search_renders_liveness_and_noaa_badges(client):
    with patch("web.routes.search_sites_by_name", side_effect=_enriched_search), \
         patch("web.routes.annotate_liveness", side_effect=_add_liveness), \
         patch("web.routes.annotate_noaa", side_effect=_add_noaa):
        response = client.post("/sites/search", data={"gauge_name": "ohio"})
    assert response.status_code == 200
    body = response.data.decode()
    assert "OHIO RIVER AT MCALPINE" in body
    assert "Reporting" in body          # live badge
    assert "No recent data" in body     # dead badge
    assert "NOAA flood forecast" in body
    # Live gauge is sorted before the dead one.
    assert body.index("03293551") < body.index("09999999")


def test_search_survives_enrichment_failure(client):
    with patch("web.routes.search_sites_by_name", side_effect=_enriched_search), \
         patch("web.routes.annotate_liveness", side_effect=Exception("boom")), \
         patch("web.routes.annotate_noaa", side_effect=Exception("boom")):
        response = client.post("/sites/search", data={"gauge_name": "ohio"})
    assert response.status_code == 200
    assert b"OHIO RIVER AT MCALPINE" in response.data  # still renders
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/web/test_sites.py -k "liveness or enrichment_failure" -v`
Expected: FAIL — `AttributeError: <module 'web.routes'> does not have the attribute 'annotate_liveness'` (not imported yet).

- [ ] **Step 3: Add imports to `web/routes.py`**

After line 17 (`from monitor.noaa_client import fetch_gauge_metadata`), add:

```python
from monitor.gauge_enrich import annotate_liveness, annotate_noaa
```

- [ ] **Step 4: Enrich and sort in the `search_sites` route**

In `web/routes.py`, in `search_sites`, replace the block from `if not matches:` through the `render_template(...)` call (lines ~326-342) with:

```python
        if not matches:
            flash(f"No gauges found matching {query!r}.", "warning")
            return redirect(url_for("sites"))

        # Best-effort enrichment — never let it break search.
        try:
            annotate_liveness(matches)
        except Exception:
            logger.warning("Liveness enrichment failed", exc_info=True)
        try:
            annotate_noaa(matches)
        except Exception:
            logger.warning("NOAA enrichment failed", exc_info=True)
        # Stable sort: live gauges first, relevance order preserved within.
        matches.sort(key=lambda m: 0 if m.get("live") else 1)

        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute("SELECT * FROM sites ORDER BY station_name")
        all_sites = cur.fetchall()
        cur.close()
        conn.close()
        return render_template(
            "sites.html",
            sites=all_sites,
            matches=matches,
            query=query,
            truncated=truncated,
        )
```

(Confirm `logger` exists at module top in `web/routes.py`; it does — it's used elsewhere.)

- [ ] **Step 5: Replace the matches block in `sites.html` with a badge table**

Replace `web/templates/sites.html` lines 17-38 (the `{% if matches %}` … `{% endif %}` block) with:

```html
{% if matches %}
<div class="mb-4">
  <label class="form-label mb-1">Matches</label>
  <table class="table table-sm align-middle">
    <thead>
      <tr><th>Station</th><th>Site #</th><th>Status</th><th>NOAA</th><th>Add</th></tr>
    </thead>
    <tbody>
    {% for m in matches %}
      <tr>
        <td>{{ m.name }}{% if m.state %} <span class="text-muted small">— {{ m.state }}</span>{% endif %}</td>
        <td><code>{{ m.number }}</code></td>
        <td>
          {% if m.live %}
            <span class="badge bg-success">Reporting{% if m.last_value is not none %} · {{ "%.1f"|format(m.last_value) }}{% endif %}</span>
          {% else %}
            <span class="badge bg-secondary">No recent data</span>
          {% endif %}
        </td>
        <td>
          {% if m.noaa_has_flood %}
            <span class="badge bg-info text-dark" title="NOAA flood forecast: {{ m.noaa_lid }}">🌊 NOAA flood forecast</span>
          {% else %}
            <span class="text-muted small">—</span>
          {% endif %}
        </td>
        <td>
          <form method="post" action="/sites/add" class="d-flex gap-1">
            <input type="hidden" name="site_number" value="{{ m.number }}">
            <select name="parameter_code" class="form-select form-select-sm" style="width:auto">
              <option value="00060">Discharge (cfs)</option>
              <option value="00065">Gage height (ft)</option>
            </select>
            <button class="btn btn-sm btn-primary">Add</button>
          </form>
        </td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% if truncated %}
  <small class="text-muted">Showing the 25 best matches — add more keywords to narrow it down.</small>
  {% endif %}
</div>
{% endif %}
```

- [ ] **Step 6: Run the sites route tests to verify they pass**

Run: `pytest tests/web/test_sites.py -v`
Expected: PASS — new badge/sort/failure tests pass. Note `test_search_renders_matches_dropdown` still expects `name="site_number"`; the new per-row form keeps that hidden input, so it still passes. `test_search_truncated_shows_hint` still finds "25 best matches".

- [ ] **Step 7: Commit**

```bash
git add web/routes.py web/templates/sites.html tests/web/test_sites.py
git commit -m "feat: enriched Sites search with liveness + NOAA badges, live-first"
```

---

### Task 5: NOAA gauge name-search in the landing-page editor

**Files:**
- Modify: `web/routes.py:433-464` (`page_add_gauge`), add new `page_search_gauges` route after it
- Modify: `web/templates/page_edit.html:11-15` (gauge add form)
- Test: `tests/web/test_pages.py`

**Interfaces:**
- Consumes: `search_sites_by_name`, `annotate_noaa`, `fetch_gauge_metadata`, `get_page_by_edit_token`, `get_page_gauges`, `get_active_page_subscribers`, `get_or_create_noaa_gauge`, `link_page_gauge`.
- Produces: `POST /edit/<edit_token>/gauges/search` renders the editor with NOAA-capable name-search results (`gauge_matches`, each with `name`, `number`, `noaa_lid`). `page_add_gauge` accepts a USGS number **or** an LID in the `lid` field, resolves the canonical LID via `fetch_gauge_metadata`, and stores it.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_pages.py` creates pages via `create_user_page(name, db_path)`, which returns `(public_token, edit_token)`. Add these tests using that helper:

```python
def _make_edit_token(client):
    from db.models import create_user_page
    db_path = client.application.config["DB_PATH"]
    _pub, edit = create_user_page("Gauge Test Page", db_path)
    return edit


def _noaa_search(*args, **kwargs):
    matches = [
        {"number": "03293551", "name": "OHIO RIVER AT MCALPINE",
         "state": "Kentucky", "site_type": "Stream"},
        {"number": "09999999", "name": "NO FORECAST CREEK",
         "state": "Kentucky", "site_type": "Stream"},
    ]
    return matches, False, ""


def _noaa_annotate(matches, *a, **k):
    for m in matches:
        if m["number"] == "03293551":
            m.update(noaa_lid="MLUK2", noaa_has_flood=True)
        else:
            m.update(noaa_lid=None, noaa_has_flood=False)
    return matches


def test_page_gauge_search_lists_only_noaa_gauges(client, tmp_db):
    edit_token = _make_edit_token(client)
    with patch("web.routes.search_sites_by_name", side_effect=_noaa_search), \
         patch("web.routes.annotate_noaa", side_effect=_noaa_annotate):
        resp = client.post(f"/edit/{edit_token}/gauges/search",
                           data={"gauge_name": "ohio"})
    body = resp.data.decode()
    assert "OHIO RIVER AT MCALPINE" in body   # has NOAA forecast → shown
    assert "NO FORECAST CREEK" not in body    # no NOAA forecast → filtered out
    assert "MLUK2" in body


def _meta_ok(identifier, timeout=10):
    return {"lid": "MLUK2", "station_name": "Ohio River at McAlpine",
            "action_stage": 21.0, "minor_flood_stage": 23.0,
            "moderate_flood_stage": 30.0, "major_flood_stage": 38.0}


def test_page_add_gauge_accepts_usgs_number_and_stores_lid(client, tmp_db):
    edit_token = _make_edit_token(client)
    with patch("web.routes.fetch_gauge_metadata", side_effect=_meta_ok):
        client.post(f"/edit/{edit_token}/gauges/add",
                    data={"lid": "03293551"}, follow_redirects=True)
    from db.models import get_all_noaa_gauges
    gauges = get_all_noaa_gauges(tmp_db)
    assert any(g["lid"] == "MLUK2" for g in gauges)  # canonical LID stored
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/web/test_pages.py -k "gauge_search or accepts_usgs_number" -v`
Expected: FAIL — 404 for the unknown `/gauges/search` route; the add test fails because the stored LID is `03293551` (raw input) not `MLUK2`.

- [ ] **Step 3: Update `page_add_gauge` to resolve the canonical LID**

In `web/routes.py`, in `page_add_gauge`, replace the body from the `lid`/`meta` handling through `link_page_gauge` with:

```python
        identifier = request.form.get("lid", "").strip()
        if not identifier:
            flash("Gauge ID is required.", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))

        meta = fetch_gauge_metadata(identifier)
        if not meta:
            flash(f"Gauge '{identifier}' not found in the NOAA database.", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))

        gauge_id = get_or_create_noaa_gauge(
            meta["lid"], meta["station_name"],
            meta["action_stage"], meta["minor_flood_stage"],
            meta["moderate_flood_stage"], meta["major_flood_stage"],
            db_path,
        )
        link_page_gauge(page["id"], gauge_id, db_path)
        flash(f"Gauge {meta['station_name']} added.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))
```

(Keep the existing `get_page_by_edit_token` 404 guard and `from db.models import ...` line at the top of the function unchanged.)

- [ ] **Step 4: Add the `page_search_gauges` route**

Immediately after `page_add_gauge` in `web/routes.py`, add:

```python
    @app.route("/edit/<edit_token>/gauges/search", methods=["POST"])
    def page_search_gauges(edit_token):
        """POST /edit/<edit_token>/gauges/search — find NOAA gauges by name.

        Runs the ranked USGS name search, keeps only results with a co-located
        NOAA flood-forecast point, and re-renders the editor with them.
        """
        db_path = current_app.config["DB_PATH"]
        from db.models import (get_page_by_edit_token, get_page_gauges,
                               get_active_page_subscribers)
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            return ("Page not found", 404)

        query = request.form.get("gauge_name", "").strip()
        gauge_matches = []
        if not query:
            flash("Enter a gauge name to search.", "danger")
        else:
            matches, _truncated, error = search_sites_by_name(query)
            if error:
                flash(error, "danger")
            else:
                try:
                    annotate_noaa(matches)
                except Exception:
                    logger.warning("NOAA enrichment failed", exc_info=True)
                gauge_matches = [m for m in matches if m.get("noaa_has_flood")]
                if not gauge_matches:
                    flash(f"No NOAA gauges found matching {query!r}.", "warning")

        gauges = get_page_gauges(page["id"], db_path)
        subscribers = get_active_page_subscribers(page["id"], db_path)
        return render_template("page_edit.html", page=page, gauges=gauges,
                              subscribers=subscribers, edit_token=edit_token,
                              gauge_matches=gauge_matches, gauge_query=query)
```

- [ ] **Step 5: Add the name-search UI to `page_edit.html`**

In `web/templates/page_edit.html`, replace the blind-LID form (lines 11-15) with a name search, the results, and a collapsed manual-LID fallback:

```html
        <form method="post" action="{{ url_for('page_search_gauges', edit_token=edit_token) }}" class="input-group mb-2">
          <input type="text" name="gauge_name" class="form-control"
                 placeholder="Search gauges by name (e.g. ohio river louisville)"
                 value="{{ gauge_query or '' }}" required>
          <button class="btn btn-secondary" type="submit">Search</button>
        </form>
        {% if gauge_matches %}
        <ul class="list-group mb-2">
          {% for m in gauge_matches %}
          <li class="list-group-item d-flex justify-content-between align-items-center">
            <span>{{ m.name }} <span class="text-muted small">({{ m.noaa_lid }})</span></span>
            <form method="post" action="{{ url_for('page_add_gauge', edit_token=edit_token) }}" class="m-0">
              <input type="hidden" name="lid" value="{{ m.noaa_lid }}">
              <button class="btn btn-sm btn-primary">Add</button>
            </form>
          </li>
          {% endfor %}
        </ul>
        {% endif %}
        <form method="post" action="{{ url_for('page_add_gauge', edit_token=edit_token) }}" class="input-group mb-3">
          <input type="text" name="lid" class="form-control text-uppercase"
                 placeholder="…or enter a NOAA LID directly (e.g. MLUK2)" required>
          <button class="btn btn-outline-secondary" type="submit">Add by ID</button>
        </form>
```

- [ ] **Step 6: Run the page tests to verify they pass**

Run: `pytest tests/web/test_pages.py -v`
Expected: PASS — new search/add tests pass; existing page-editor tests still pass (the editor still renders `gauges`, `subscribers`, and a `lid` add form).

- [ ] **Step 7: Commit**

```bash
git add web/routes.py web/templates/page_edit.html tests/web/test_pages.py
git commit -m "feat: NOAA gauge name-search in landing-page editor; store canonical LID"
```

---

### Task 6: Full suite + live-API smoke check

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pytest`
Expected: PASS — all tests green, no regressions.

- [ ] **Step 2: Smoke-check the NOAA bridge against the live API**

Run: `python -c "from monitor.noaa_client import fetch_gauge_metadata; print(fetch_gauge_metadata('03293551'))"`
Expected: a dict with `lid='MLUK2'`, a station name, and non-None flood stages — confirming the USGS-number → NOAA-LID bridge and dict-shape parsing work against the real API.

- [ ] **Step 3: Smoke-check liveness against the live API**

Run: `python -c "from monitor.gauge_enrich import annotate_liveness; print(annotate_liveness([{'number':'03294500','name':'x'}]))"`
Expected: the match shows `live=True` with a `last_value` (03294500 is an active Ohio River gauge). If USGS is briefly unreachable it returns `live=False` without raising — re-run to confirm.

- [ ] **Step 4: Commit any final cleanup (if needed)**

```bash
git commit --allow-empty -m "chore: gauge discovery verified end-to-end"
```

---

## Self-Review

**Spec coverage:**
- Dead USGS gauges flagged → Task 2 (`annotate_liveness`) + Task 4 (badges, live-first sort). ✓
- NOAA discoverable by name → Task 3 (`annotate_noaa`) + Task 5 (editor name search). ✓
- `usgsId` bridge (NWPS accepts USGS number) → Task 1 (`fetch_gauge_metadata` accepts identifier) + Task 3 (probe by number). ✓
- `fetch_gauge_metadata` returns `lid` → Task 1. ✓
- Best-effort/graceful degradation → wrapped calls in Tasks 4 & 5, unchanged-on-failure in Tasks 2 & 3, with explicit failure tests. ✓
- Enrich the shown results only (≤25) → route passes the ranked `matches` list to the annotators. ✓
- Testing per spec → each task is TDD with the exact tests listed. ✓
- Out of scope respected → no new providers, no subsystem merge, no persistent NOAA cache. ✓

**Deviation from spec (intentional, noted):** the dead-gauge badge reads "No recent data" without an exact last-seen date. The batch liveness window returns no rows for dead gauges, so a precise last-seen date would need an extra wide-window call per dead gauge — omitted to keep enrichment to a single batch call, consistent with the spec's performance intent.

**Placeholder scan:** none — every code and test step is complete.

**Type consistency:** `fetch_gauge_metadata(identifier, timeout=…)` returns `lid` (Task 1), consumed by `annotate_noaa` (Task 3) and `page_add_gauge` (Task 5). `annotate_liveness`/`annotate_noaa` field names (`live`, `last_value`, `last_time`, `noaa_lid`, `noaa_has_flood`) are set in Tasks 2/3 and read identically in the templates and route tests in Tasks 4/5. ✓
