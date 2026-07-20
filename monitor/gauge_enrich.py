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
