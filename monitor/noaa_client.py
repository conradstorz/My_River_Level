"""
NOAA National Water Prediction Service (NWPS) API client.

Fetches gauge metadata and flood-category thresholds, retrieves the current
observed stage, and classifies flood severity (Normal / Action / Minor /
Moderate / Major) from those thresholds.
"""

import logging
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
        station_name, lid, action_stage, minor_flood_stage,
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
