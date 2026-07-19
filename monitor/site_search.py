"""
USGS gauge search by keywords, with relevance ranking.

Uses the USGS Monitoring Locations OGC API (substring name search via CQL2
LIKE). The API has no relevance ranking and only matches exact substrings, so
this module:

  * parses the user's text into keywords (any order, punctuation/case ignored),
  * expands full US state names to the abbreviation USGS uses in names
    (e.g. "kentucky" also matches "..., KY"),
  * retrieves candidates that contain all keywords, relaxing to the keywords
    that DO match when some don't (typos / stray words),
  * ranks candidates locally with difflib fuzzy matching so near-misses like
    "lousville" still score highly against "LOUISVILLE".

Called from the web UI's /sites/search route.
"""

import logging
import re
from difflib import SequenceMatcher

import requests

logger = logging.getLogger(__name__)

_API_URL = (
    "https://api.waterdata.usgs.gov/ogcapi/v0"
    "/collections/monitoring-locations/items"
)
_NAME = "monitoring_location_name"
_SITE_TYPES = "site_type_code IN ('ST','LK')"  # Stream, Lake/Reservoir
_TIMEOUT_SECONDS = 15
_FETCH_CAP = 300  # candidate pool size to rank locally

# Single-word US state names -> the abbreviation USGS uses at the end of names.
# Multi-word states (e.g. "new york") are intentionally omitted; type "ny".
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


def _escape(text):
    """Escape a keyword for a CQL2 LIKE string literal.

    Drop the LIKE wildcards (% and _) so they are literal, and double single
    quotes so the value can't break out of the quoted literal.
    """
    return text.replace("%", "").replace("_", "").replace("'", "''")


def _tokenize(query):
    """Split user text into uppercase keyword tokens (>= 2 chars)."""
    return [t for t in re.split(r"[^A-Z0-9]+", query.upper()) if len(t) >= 2]


def _match_group(token):
    """CQL2 fragment requiring `token` to appear in the name.

    For a full state name, also accept the ", XX" abbreviation form USGS uses,
    as an OR alternative — this never *forces* the state reading, so rivers
    named after states (e.g. "OHIO RIVER") still match on the literal word.
    """
    esc = _escape(token)
    if token in _STATE_ABBR:
        abbr = _STATE_ABBR[token]
        return f"({_NAME} LIKE '%{esc}%' OR {_NAME} LIKE '%, {abbr}%')"
    return f"{_NAME} LIKE '%{esc}%'"


def _fetch(cql, limit):
    """Run one API query for `cql` (site-type filter is appended here).

    Returns (features, error): a list of GeoJSON features and "" on success,
    or ([], message) on any failure. Never raises.
    """
    params = {
        "f": "json",
        "limit": limit,
        # Do NOT send filter-lang — the API 400s on it and defaults to
        # cql2-text for `filter`. Verified against the live API.
        "filter": f"{cql} AND {_SITE_TYPES}",
        "sortby": _NAME,
    }
    try:
        resp = requests.get(_API_URL, params=params, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        logger.warning("USGS site search timed out")
        return [], "USGS search timed out. Please try again."
    except requests.exceptions.RequestException as e:
        logger.warning("USGS site search failed: %s", e)
        return [], "USGS search failed. Please try again later."
    except ValueError:
        return [], "USGS search returned an unexpected response."

    if not isinstance(data, dict):
        return [], "USGS search returned an unexpected response."
    features = data.get("features", []) or []
    if not isinstance(features, list):
        features = []
    return features, ""


def _score(name, tokens):
    """Relevance score of a station name against the query tokens.

    Each token contributes up to 1.0: 1.0 for an exact substring (or a state
    name whose abbreviation is present), otherwise the best difflib similarity
    of the token to any single word in the name (so typos score high but not
    perfect). Higher total = better match.
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


def _to_match(feat):
    """Convert a GeoJSON feature to a match dict, or None if unusable."""
    if not isinstance(feat, dict):
        return None
    props = feat.get("properties", {}) or {}
    number = props.get("monitoring_location_number")
    if not number:
        return None
    return {
        "number": number,
        "name": props.get("monitoring_location_name", "") or "",
        "state": props.get("state_name", "") or "",
        "site_type": props.get("site_type", "") or "",
    }


def search_sites_by_name(query, limit=25):
    """
    Search USGS monitoring locations by keywords, ranked best-first.

    Returns (matches, truncated, error):
      matches:   list of {number, name, state, site_type}, <= limit, ranked by
                 relevance (most likely first)
      truncated: True if more candidates were found than returned
      error:     "" on success, else a human-readable message
    """
    if not query or not query.strip():
        return [], False, "Enter a gauge name to search."
    tokens = _tokenize(query)
    if not tokens:
        return [], False, "Enter a gauge name to search."
    groups = [_match_group(t) for t in tokens]

    # Step 1: every keyword must appear (order-independent).
    features, error = _fetch(" AND ".join(groups), _FETCH_CAP)
    if error:
        return [], False, error

    # Step 2: relax for typos / stray words — anchor on the keywords that do
    # match, and let fuzzy ranking account for the ones that don't.
    if not features:
        anchors = []
        for group in groups:
            found, err = _fetch(group, 1)
            if err:
                return [], False, err
            if found:
                anchors.append(group)
        if anchors:
            features, error = _fetch(" AND ".join(anchors), _FETCH_CAP)
            if error:
                return [], False, error

    candidates = [m for m in (_to_match(f) for f in features) if m]
    candidates.sort(key=lambda m: (-_score(m["name"], tokens),
                                   len(m["name"]), m["name"]))
    truncated = len(candidates) > limit
    return candidates[:limit], truncated, ""
