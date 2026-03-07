"""
USGS site validation.

Called at registration time (web UI add_site route) to reject site numbers
that don't exist in the USGS database before they pollute the polling loop.
"""

import logging
import dataretrieval.nwis as nwis

logger = logging.getLogger(__name__)


def validate_usgs_site(site_number, parameter_code="00060"):
    """
    Verify a site number exists in the USGS database.

    Returns:
        (is_valid: bool, station_name: str, error: str)

    On success, station_name is the authoritative name from USGS.
    On failure, station_name is '' and error describes why.
    """
    try:
        df, _ = nwis.get_info(sites=site_number)
    except Exception as e:
        msg = str(e)
        if "Page Not Found" in msg or "empty query" in msg:
            return False, "", f"Site {site_number!r} not found in USGS database"
        logger.warning("USGS lookup failed for site %s: %s", site_number, msg)
        return False, "", f"USGS lookup failed: {msg}"

    if df is None or len(df) == 0:
        return False, "", f"Site {site_number!r} not found in USGS database"

    station_name = ""
    if "station_nm" in df.columns:
        station_name = str(df["station_nm"].values[0]).strip()

    return True, station_name, ""
