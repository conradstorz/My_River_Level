# Add a Site by Gauge Name — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users add a monitored site by typing a gauge name; when several gauges match, show a dropdown to pick from.

**Architecture:** A new `monitor/site_search.py` queries the USGS Monitoring Locations OGC API (substring name search via CQL2 `LIKE`) over HTTPS with `requests`. A new `POST /sites/search` route renders the existing `sites.html` with a dropdown of matches; the dropdown submits the chosen site number to the unchanged `POST /sites/add`, which keeps its authoritative validation. Pure server-rendered Flask + Bootstrap, no JavaScript.

**Tech Stack:** Python, Flask, Jinja2, Bootstrap, `requests` (already a dependency), pytest + `unittest.mock`.

## Global Constraints

- Never chain shell commands (`&&`, `||`, `|`, `;`) — one command per call.
- Tests require the shared PostgreSQL server to be running (`shared-postgres` project: `docker compose up -d`).
- The test image bakes source + tests in via `Dockerfile.test`, so **every test run must pass `--build`** to pick up changes.
- Test command template (from the `My_River_level` repo root):
  `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest <target> -v`
- USGS Monitoring Locations API base URL: `https://api.waterdata.usgs.gov/ogcapi/v0/collections/monitoring-locations/items`
- Relevant `site_type_code` values: `ST` (Stream), `LK` (Lake, Reservoir, Impoundment).
- API property names: `monitoring_location_number`, `monitoring_location_name`, `state_name`, `site_type`.

---

### Task 1: USGS name-search backend

**Files:**
- Create: `monitor/site_search.py`
- Test: `tests/monitor/test_site_search.py`

**Interfaces:**
- Consumes: nothing from other tasks; uses `requests`.
- Produces: `search_sites_by_name(query: str, limit: int = 25) -> tuple[list[dict], bool, str]`
  - Returns `(matches, truncated, error)`.
  - `matches`: list of `{"number": str, "name": str, "state": str, "site_type": str}`, at most `limit`, sorted by `name`.
  - `truncated`: `True` when more than `limit` matches exist.
  - `error`: `""` on success, else a human-readable message. Never raises.

- [ ] **Step 1: Write the failing tests**

Create `tests/monitor/test_site_search.py`:

```python
"""Tests for USGS gauge search by station name (Monitoring Locations OGC API)."""

from unittest.mock import patch, MagicMock

import requests

from monitor.site_search import search_sites_by_name


def _feature(number, name, state="Kentucky", site_type="Stream"):
    return {
        "properties": {
            "monitoring_location_number": number,
            "monitoring_location_name": name,
            "state_name": state,
            "site_type": site_type,
        }
    }


def _fake_response(features, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {"features": features}

    def _raise():
        if status >= 400:
            raise requests.exceptions.HTTPError(f"{status} error")

    resp.raise_for_status.side_effect = _raise
    return resp


def test_single_match_returns_one_result():
    features = [_feature("03294500", "OHIO RIVER AT LOUISVILLE, KY")]
    with patch("monitor.site_search.requests.get", return_value=_fake_response(features)):
        matches, truncated, error = search_sites_by_name("ohio river at louisville")
    assert error == ""
    assert truncated is False
    assert matches == [{
        "number": "03294500",
        "name": "OHIO RIVER AT LOUISVILLE, KY",
        "state": "Kentucky",
        "site_type": "Stream",
    }]


def test_multiple_matches_are_sorted_by_name():
    features = [
        _feature("222", "MILL CREEK NEAR B"),
        _feature("111", "MILL CREEK NEAR A"),
    ]
    with patch("monitor.site_search.requests.get", return_value=_fake_response(features)):
        matches, truncated, error = search_sites_by_name("mill creek")
    assert [m["name"] for m in matches] == ["MILL CREEK NEAR A", "MILL CREEK NEAR B"]
    assert truncated is False


def test_more_than_limit_sets_truncated_and_caps_results():
    features = [_feature(str(i), f"CREEK {i}") for i in range(3)]
    with patch("monitor.site_search.requests.get", return_value=_fake_response(features)):
        matches, truncated, error = search_sites_by_name("creek", limit=2)
    assert len(matches) == 2
    assert truncated is True
    assert error == ""


def test_zero_matches_returns_empty_without_error():
    with patch("monitor.site_search.requests.get", return_value=_fake_response([])):
        matches, truncated, error = search_sites_by_name("zzzznotagauge")
    assert matches == []
    assert truncated is False
    assert error == ""


def test_http_error_returns_error_message():
    with patch("monitor.site_search.requests.get", return_value=_fake_response([], status=500)):
        matches, truncated, error = search_sites_by_name("ohio")
    assert matches == []
    assert error != ""


def test_timeout_returns_error_message():
    with patch("monitor.site_search.requests.get",
               side_effect=requests.exceptions.Timeout("slow")):
        matches, truncated, error = search_sites_by_name("ohio")
    assert matches == []
    assert "timed out" in error.lower()


def test_empty_query_returns_error_and_skips_api():
    with patch("monitor.site_search.requests.get") as mock_get:
        matches, truncated, error = search_sites_by_name("   ")
    assert matches == []
    assert error != ""
    mock_get.assert_not_called()


def test_query_is_uppercased_wildcards_stripped_and_quotes_escaped():
    with patch("monitor.site_search.requests.get", return_value=_fake_response([])) as mock_get:
        search_sites_by_name("o'brien 50%_x")
    sent_filter = mock_get.call_args.kwargs["params"]["filter"]
    # Uppercased, single quote doubled, % and _ removed, site types constrained.
    assert "O''BRIEN 50X" in sent_filter
    assert "site_type_code IN ('ST','LK')" in sent_filter
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_site_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitor.site_search'`.

- [ ] **Step 3: Write the implementation**

Create `monitor/site_search.py`:

```python
"""
USGS gauge search by station name.

Uses the USGS Monitoring Locations OGC API, which supports substring name
search (the older dataretrieval NWIS site service does not). Called from the
web UI's /sites/search route.
"""

import logging

import requests

logger = logging.getLogger(__name__)

_API_URL = (
    "https://api.waterdata.usgs.gov/ogcapi/v0"
    "/collections/monitoring-locations/items"
)
_TIMEOUT_SECONDS = 15


def _sanitize(query):
    """Prepare user text for a CQL2 LIKE string literal.

    USGS names are uppercase and CQL2 LIKE is case-sensitive, so uppercase the
    query. Remove the LIKE wildcards (% and _) so they match literally-nothing
    special, and double single quotes so the value can't break out of the
    quoted literal.
    """
    text = query.strip().upper()
    text = text.replace("%", "").replace("_", "")
    text = text.replace("'", "''")
    return text


def search_sites_by_name(query, limit=25):
    """
    Search USGS monitoring locations by (partial) station name.

    Returns (matches, truncated, error):
      matches:   list of {number, name, state, site_type}, <= limit, sorted by name
      truncated: True if more than `limit` matches exist
      error:     "" on success, else a human-readable message
    """
    if not query or not query.strip():
        return [], False, "Enter a gauge name to search."

    needle = _sanitize(query)
    cql = (
        f"monitoring_location_name LIKE '%{needle}%' "
        f"AND site_type_code IN ('ST','LK')"
    )
    params = {
        "f": "json",
        "limit": limit + 1,  # one extra row to detect truncation
        "filter-lang": "cql2-text",
        "filter": cql,
        "sortby": "monitoring_location_name",
    }

    try:
        resp = requests.get(_API_URL, params=params, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        return [], False, "USGS search timed out. Please try again."
    except requests.exceptions.RequestException as e:
        logger.warning("USGS site search failed for %r: %s", query, e)
        return [], False, "USGS search failed. Please try again later."
    except ValueError:
        return [], False, "USGS search returned an unexpected response."

    features = data.get("features", []) or []
    truncated = len(features) > limit

    matches = []
    for feat in features[:limit]:
        props = feat.get("properties", {}) or {}
        number = props.get("monitoring_location_number")
        if not number:
            continue
        matches.append({
            "number": number,
            "name": props.get("monitoring_location_name", ""),
            "state": props.get("state_name", ""),
            "site_type": props.get("site_type", ""),
        })
    matches.sort(key=lambda m: m["name"])
    return matches, truncated, ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/monitor/test_site_search.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add monitor/site_search.py tests/monitor/test_site_search.py
git commit -m "feat: add USGS gauge search by station name"
```

---

### Task 2: Search route + template

**Files:**
- Modify: `web/routes.py` (add import at line 6 area; add route after `add_site`, ~line 266)
- Modify: `web/templates/sites.html`
- Test: `tests/web/test_sites.py`

**Interfaces:**
- Consumes: `search_sites_by_name(query, limit=25) -> (matches, truncated, error)` from Task 1.
- Produces: `POST /sites/search` form endpoint reading form field `gauge_name`; renders `sites.html` with template vars `matches`, `query`, `truncated`. The results form submits `site_number` + `parameter_code` to the existing `POST /sites/add`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_sites.py`:

```python
def _search_result(*args, **kwargs):
    matches = [{
        "number": "03294500",
        "name": "OHIO RIVER AT LOUISVILLE, KY",
        "state": "Kentucky",
        "site_type": "Stream",
    }]
    return matches, False, ""


def test_search_renders_matches_dropdown(client):
    with patch("web.routes.search_sites_by_name", side_effect=_search_result):
        response = client.post("/sites/search", data={"gauge_name": "ohio river"})
    assert response.status_code == 200
    assert b"OHIO RIVER AT LOUISVILLE, KY" in response.data
    assert b"03294500" in response.data
    assert b'name="site_number"' in response.data


def test_search_no_matches_flashes(client):
    with patch("web.routes.search_sites_by_name", return_value=([], False, "")):
        response = client.post(
            "/sites/search", data={"gauge_name": "zzzznotagauge"},
            follow_redirects=True,
        )
    assert response.status_code == 200
    assert b"No gauges found" in response.data


def test_search_api_error_flashes(client):
    with patch("web.routes.search_sites_by_name",
               return_value=([], False, "USGS search failed. Please try again later.")):
        response = client.post(
            "/sites/search", data={"gauge_name": "ohio"},
            follow_redirects=True,
        )
    assert response.status_code == 200
    assert b"USGS search failed" in response.data


def test_search_empty_query_flashes(client):
    response = client.post(
        "/sites/search", data={"gauge_name": "   "},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Enter a gauge name" in response.data


def test_search_truncated_shows_hint(client):
    with patch("web.routes.search_sites_by_name",
               return_value=(_search_result()[0], True, "")):
        response = client.post("/sites/search", data={"gauge_name": "creek"})
    assert b"Showing first 25" in response.data
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_sites.py -v`
Expected: FAIL — `/sites/search` returns 404 / `search_sites_by_name` not importable from `web.routes`.

- [ ] **Step 3: Add the import and route**

In `web/routes.py`, add after line 6 (`from monitor.site_validation import validate_usgs_site`):

```python
from monitor.site_search import search_sites_by_name
```

In `web/routes.py`, add this route immediately after the `add_site` function (after its `return redirect(url_for("sites"))`, ~line 266):

```python
    @app.route("/sites/search", methods=["POST"])
    def search_sites():
        db_path = current_app.config["DB_PATH"]
        query = request.form.get("gauge_name", "").strip()
        if not query:
            flash("Enter a gauge name to search.", "danger")
            return redirect(url_for("sites"))

        matches, truncated, error = search_sites_by_name(query)
        if error:
            flash(error, "danger")
            return redirect(url_for("sites"))
        if not matches:
            flash(f"No gauges found matching {query!r}.", "warning")
            return redirect(url_for("sites"))

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

- [ ] **Step 4: Update the template**

Replace the entire contents of `web/templates/sites.html` with:

```html
{% extends "base.html" %}
{% block title %}Sites{% endblock %}
{% block content %}
<h1 class="mb-4">Monitored Sites</h1>

<h4>Find a Gauge by Name</h4>
<form method="post" action="/sites/search" class="row g-2 mb-3">
  <div class="col-md-8">
    <input name="gauge_name" class="form-control"
           placeholder="Gauge name (e.g. Ohio River at Louisville)"
           value="{{ query or '' }}" required>
  </div>
  <div class="col-md-2"><button class="btn btn-secondary w-100">Search</button></div>
</form>

{% if matches %}
<form method="post" action="/sites/add" class="row g-2 mb-4 align-items-center">
  <div class="col-12"><label class="form-label mb-1">Matches</label></div>
  <div class="col-md-6">
    <select name="site_number" class="form-select">
      {% for m in matches %}
      <option value="{{ m.number }}">{{ m.name }} — {{ m.number }}{% if m.state %}, {{ m.state }}{% endif %}</option>
      {% endfor %}
    </select>
  </div>
  <div class="col-md-3">
    <select name="parameter_code" class="form-select">
      <option value="00060">Discharge (cfs)</option>
      <option value="00065">Gage height (ft)</option>
    </select>
  </div>
  <div class="col-md-3"><button class="btn btn-primary w-100">Add selected</button></div>
  {% if truncated %}
  <div class="col-12"><small class="text-muted">Showing first 25 matches — refine your search (add a state or town).</small></div>
  {% endif %}
</form>
{% endif %}

<h4>Add Site by USGS Number</h4>
<form method="post" action="/sites/add" class="row g-2 mb-4">
  <div class="col-md-3"><input name="site_number" class="form-control" placeholder="USGS Site # (e.g. 03277200)" required></div>
  <div class="col-md-4"><input name="station_name" class="form-control" placeholder="Station name (optional)"></div>
  <div class="col-md-2">
    <select name="parameter_code" class="form-select">
      <option value="00060">Discharge (cfs)</option>
      <option value="00065">Gage height (ft)</option>
    </select>
  </div>
  <div class="col-md-2"><button class="btn btn-primary w-100">Add</button></div>
</form>

{% if sites %}
<table class="table table-striped">
  <thead><tr><th>Site #</th><th>Station Name</th><th>Parameter</th><th>Status</th><th>Actions</th></tr></thead>
  <tbody>
  {% for s in sites %}
  <tr>
    <td><code>{{ s.site_number }}</code></td>
    <td>{{ s.station_name or "—" }}</td>
    <td>{{ "Discharge" if s.parameter_code == "00060" else "Gage height" }}</td>
    <td>
      <form method="post" action="/sites/{{ s.id }}/toggle" style="display:inline">
        <button class="btn btn-sm {{ 'btn-success' if s.active else 'btn-outline-secondary' }}">
          {{ "Active" if s.active else "Inactive" }}
        </button>
      </form>
    </td>
    <td>
      <form method="post" action="/sites/{{ s.id }}/remove" style="display:inline">
        <button class="btn btn-sm btn-outline-danger">Remove</button>
      </form>
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="text-muted">No sites configured yet.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Run the sites tests to verify they pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_sites.py -v`
Expected: PASS (all sites tests, new and existing).

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest -q`
Expected: PASS (119 passed: 106 existing + 8 from Task 1 + 5 from Task 2).

- [ ] **Step 7: Commit**

```bash
git add web/routes.py web/templates/sites.html tests/web/test_sites.py
git commit -m "feat: add /sites/search route and gauge-name search UI"
```

---

## Manual verification (after both tasks)

With the app running (`docker compose up -d`) and reachable at `http://hpz440:5743/sites`:

1. In "Find a Gauge by Name", type `ohio river at louisville`, click Search → dropdown shows `OHIO RIVER AT LOUISVILLE, KY — 03294500, Kentucky`.
2. Click "Add selected" → the site appears in the table below.
3. Type a common name like `mill creek`, Search → dropdown lists multiple matches and the "Showing first 25" hint appears if truncated.
4. Type gibberish (`zzzz`), Search → "No gauges found" flash.
