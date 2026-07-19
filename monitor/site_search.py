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
    if not needle:
        return [], False, "Enter a gauge name to search."

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
        logger.warning("USGS site search timed out for %r", query)
        return [], False, "USGS search timed out. Please try again."
    except requests.exceptions.RequestException as e:
        logger.warning("USGS site search failed for %r: %s", query, e)
        return [], False, "USGS search failed. Please try again later."
    except ValueError:
        return [], False, "USGS search returned an unexpected response."

    if not isinstance(data, dict):
        return [], False, "USGS search returned an unexpected response."

    features = data.get("features", []) or []
    if not isinstance(features, list):
        features = []
    truncated = len(features) > limit

    matches = []
    for feat in features[:limit]:
        if not isinstance(feat, dict):
            continue
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
