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
