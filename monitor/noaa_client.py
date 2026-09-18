"""
NOAA National Water Prediction Service (NWPS) API client.

Fetches gauge metadata and flood-category thresholds, retrieves the current
observed stage and the published forecast, and classifies flood severity
(Normal / Action / Minor / Moderate / Major) from those thresholds.

Every fetch degrades to None rather than raising: the polling threads treat a
missing answer as "nothing to record this pass" and try again next time.
"""

import logging
import math
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

NWPS_BASE = "https://api.water.noaa.gov/nwps/v1"
TIMEOUT = 10


def classify_noaa_condition(stage, action_stage, minor_stage, moderate_stage, major_stage):
    """Map current stage to a severity label using NOAA flood category thresholds."""
    if stage is None:
        return "Unknown"
    if major_stage is not None and stage >= major_stage:
        return "Major"
    if moderate_stage is not None and stage >= moderate_stage:
        return "Moderate"
    if minor_stage is not None and stage >= minor_stage:
        return "Minor"
    if action_stage is not None and stage >= action_stage:
        return "Action"
    return "Normal"


def fetch_gauge_metadata(identifier, timeout=TIMEOUT):
    """
    Fetch station name, canonical LID, and flood thresholds from NWPS.

    `identifier` may be a NOAA LID or a USGS site number — the NWPS
    /gauges/{id} endpoint resolves both.

    NWPS returns flood categories either as a dict keyed by category name
    ({"action": {"stage": 21.0}, ...}) or as a list of {"name", "stage"}.
    Both shapes are handled.

    Returns a dict with keys:
        station_name, lid, usgs_id, action_stage, minor_flood_stage,
        moderate_flood_stage, major_flood_stage
    or None on error.
    """
    try:
        url = f"{NWPS_BASE}/gauges/{identifier.lower()}"
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
        "usgs_id": data.get("usgsId"),
        **thresholds,
    }


def fetch_current_stage(lid):
    """
    Fetch the most recent observed stage from the NWPS stageflow endpoint.
    Returns a float (feet) or None.
    """
    url = f"{NWPS_BASE}/gauges/{lid.lower()}/stageflow/observed"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            logger.warning("NOAA stage fetch failed for %s: HTTP %s", lid, resp.status_code)
            return None
        data = resp.json()
        readings = data.get("data", [])
        if not readings:
            return None
        return readings[-1].get("primary")
    except Exception:
        logger.exception("Error fetching NOAA stage for %s", lid)
        return None


_MILES_PER_DEGREE_LAT = 69.0


def _gauge_from_payload(item):
    """Normalise one NWPS gauge listing entry, or None if unusable."""
    if not isinstance(item, dict) or not item.get("lid"):
        return None
    lat = item.get("latitude", item.get("lat"))
    lon = item.get("longitude", item.get("lon"))
    if lat is None or lon is None:
        return None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    usgs_id = item.get("usgsId") or None
    return {
        "lid": str(item["lid"]).upper(),
        "name": item.get("name") or item["lid"],
        "usgs_id": str(usgs_id) if usgs_id else None,
        "lat": lat,
        "lon": lon,
    }


def gauges_near(lat, lon, radius_miles, timeout=TIMEOUT):
    """List NWPS gauges inside a bounding box of `radius_miles` around a point.

    Returns [{lid, name, usgs_id, lat, lon}] — an empty list on any failure,
    because a discovery step that cannot list NOAA gauges should degrade to
    "no NOAA gauges proposed", not abort the whole pin flow.
    """
    dlat = radius_miles / _MILES_PER_DEGREE_LAT
    dlon = radius_miles / (_MILES_PER_DEGREE_LAT * max(math.cos(math.radians(lat)), 0.01))
    params = {
        "bbox.xmin": lon - dlon, "bbox.ymin": lat - dlat,
        "bbox.xmax": lon + dlon, "bbox.ymax": lat + dlat,
        "srid": "EPSG_4326",
    }
    try:
        resp = requests.get(f"{NWPS_BASE}/gauges", params=params, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("NWPS gauge listing failed: HTTP %s", resp.status_code)
            return []
        data = resp.json()
    except Exception:
        logger.exception("Error listing NWPS gauges near %s,%s", lat, lon)
        return []
    items = data.get("gauges") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    rows = [_gauge_from_payload(item) for item in items]
    return [r for r in rows if r]


# ── Forecast ────────────────────────────────────────────────────────────────

_ISSUED_KEYS = ("issuedTime", "issuanceTime", "issued_at", "issued", "issuedAt")
_VALID_KEYS = ("validTime", "valid_at", "validtime", "time", "timestamp")
_STAGE_KEYS = ("primary", "stage", "value")
_SERIES_KEYS = ("data", "forecast", "points", "series")


def parse_nwps_time(value):
    """Parse an NWPS ISO 8601 timestamp into a timezone-aware datetime.

    NWPS writes times as ``2026-08-12T12:00:00Z``; ``fromisoformat`` before
    Python 3.11 chokes on the ``Z``, and a naive datetime would compare
    wrongly against the TIMESTAMPTZ columns the forecast archive uses, so
    anything without an offset is treated as UTC. Returns None if unparseable.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _first_key(mapping, keys):
    """Return the first present, non-None value among `keys`."""
    for key in keys:
        if isinstance(mapping, dict) and mapping.get(key) is not None:
            return mapping[key]
    return None


def _extract_series(payload):
    """Pull the list of forecast readings out of whichever shape NWPS sent.

    Seen in the wild: a bare list, ``{"data": [...]}``, and a named section
    such as ``{"forecast": {"data": [...]}}`` — the same dict-vs-list
    variability `fetch_gauge_metadata` already copes with.
    """
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in _SERIES_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_series(value)
            if nested:
                return nested
    return []


def _extract_issued_at(payload):
    """Find the forecast's issue time anywhere in the payload, or None."""
    if isinstance(payload, dict):
        issued = parse_nwps_time(_first_key(payload, _ISSUED_KEYS))
        if issued is not None:
            return issued
        for key in _SERIES_KEYS:
            value = payload.get(key)
            if isinstance(value, dict):
                issued = _extract_issued_at(value)
                if issued is not None:
                    return issued
    return None


def fetch_forecast_result(lid, timeout=TIMEOUT):
    """
    Fetch `lid`'s forecast and say *why* there isn't one when there isn't.

    Returns ``{"status": ..., "forecast": dict | None}`` where status is:

    ``"ok"``
        A forecast was retrieved; ``forecast`` holds it.
    ``"none"``
        NOAA answered authoritatively that this gauge has no forecast — an
        HTTP 404, or a 200 carrying an empty series.
    ``"error"``
        We could not find out: a network failure, a 5xx, or an unparseable
        payload.

    The distinction matters because the portal grades gauges. Treating an
    outage as "publishes no forecast" would tell a user their gauge gives no
    advance warning when in fact we simply failed to ask.
    """
    if not isinstance(lid, str) or not lid.strip():
        return {"status": "error", "forecast": None}
    try:
        url = f"{NWPS_BASE}/gauges/{lid.lower()}/stageflow/forecast"
        resp = requests.get(url, timeout=timeout)
    except Exception:
        logger.exception("Error fetching NOAA forecast for %s", lid)
        return {"status": "error", "forecast": None}

    if resp.status_code == 404:
        logger.info("NOAA publishes no forecast for %s (HTTP 404)", lid)
        return {"status": "none", "forecast": None}
    if resp.status_code != 200:
        logger.warning("NOAA forecast fetch failed for %s: HTTP %s",
                       lid, resp.status_code)
        return {"status": "error", "forecast": None}

    forecast = _parse_forecast_payload(lid, resp)
    if forecast is None:
        return {"status": "error", "forecast": None}
    if not forecast["points"]:
        logger.info("NOAA returned an empty forecast series for %s", lid)
        return {"status": "none", "forecast": None}
    return {"status": "ok", "forecast": forecast}


def _parse_forecast_payload(lid, resp):
    """Turn a 200 forecast response into {"issued_at", "points"}, or None.

    None means the payload could not be understood at all. An understood
    payload that simply carries no readings returns an empty ``points`` list —
    callers must treat those two cases differently.
    """
    try:
        payload = resp.json()
    except Exception:
        logger.exception("Unparseable NOAA forecast payload for %s", lid)
        return None

    try:
        points = []
        for reading in _extract_series(payload):
            if not isinstance(reading, dict):
                continue
            valid_at = parse_nwps_time(_first_key(reading, _VALID_KEYS))
            stage = _first_key(reading, _STAGE_KEYS)
            if valid_at is None or stage is None:
                continue
            try:
                points.append({"valid_at": valid_at, "stage": float(stage)})
            except (TypeError, ValueError):
                continue
    except Exception:
        logger.exception("Error parsing NOAA forecast for %s", lid)
        return None

    issued_at = _extract_issued_at(payload) or datetime.now(timezone.utc)
    return {"issued_at": issued_at, "points": points}


def fetch_forecast(lid, timeout=TIMEOUT):
    """
    Fetch the published stage forecast for `lid` from NWPS.

    Returns ``{"issued_at": datetime, "points": [{"valid_at": datetime,
    "stage": float}, ...]}`` with timezone-aware datetimes, or None on any
    error or when the gauge publishes no forecast. Readings missing a usable
    time or stage are skipped rather than failing the whole fetch.

    Use :func:`fetch_forecast_result` when you need to tell "NOAA has no
    forecast" apart from "we could not reach NOAA".
    """
    return fetch_forecast_result(lid, timeout=timeout)["forecast"]
