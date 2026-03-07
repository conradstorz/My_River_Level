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


def fetch_gauge_metadata(lid):
    """
    Fetch station name and flood thresholds from the NWPS gauge endpoint.

    The NWPS API returns flood categories as a list under data["flood"]["categories"].
    Each item has "name" (action/minor/moderate/major) and "stage" (float, feet).

    Returns a dict with keys:
        station_name, action_stage, minor_flood_stage,
        moderate_flood_stage, major_flood_stage
    or None on error.
    """
    url = f"{NWPS_BASE}/gauges/{lid.lower()}"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            logger.warning("NOAA metadata fetch failed for %s: HTTP %s", lid, resp.status_code)
            return None
        data = resp.json()
    except Exception:
        logger.exception("Error fetching NOAA metadata for %s", lid)
        return None

    thresholds = {"action_stage": None, "minor_flood_stage": None,
                  "moderate_flood_stage": None, "major_flood_stage": None}
    categories = (data.get("flood") or {}).get("categories", [])
    key_map = {
        "action":   "action_stage",
        "minor":    "minor_flood_stage",
        "moderate": "moderate_flood_stage",
        "major":    "major_flood_stage",
    }
    for cat in categories:
        name = (cat.get("name") or "").lower()
        if name in key_map:
            thresholds[key_map[name]] = cat.get("stage")

    return {
        "station_name": data.get("name", lid),
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
