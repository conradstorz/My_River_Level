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
