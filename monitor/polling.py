import threading
import time
import queue
import logging
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import dataretrieval.nwis as nwis

from db.models import get_db, get_setting

logger = logging.getLogger(__name__)


def get_active_sites(db_path=None):
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT id, site_number, station_name, parameter_code FROM sites WHERE active=1"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_previous_severity(site_id, db_path=None):
    conn = get_db(db_path)
    row = conn.execute(
        "SELECT severity FROM site_conditions WHERE site_id=? ORDER BY id DESC LIMIT 1",
        (site_id,)
    ).fetchone()
    conn.close()
    return row["severity"] if row else None


def record_condition(site_id, current_value, unit, percentile, severity, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        """INSERT INTO site_conditions
           (site_id, current_value, unit, percentile, severity)
           VALUES (?, ?, ?, ?, ?)""",
        (site_id, current_value, unit, percentile, severity)
    )
    conn.commit()
    conn.close()


def detect_transition(previous_severity, new_severity):
    """Returns (old, new) tuple if severity changed, else None."""
    if previous_severity == new_severity:
        return None
    return (previous_severity, new_severity)


def classify_condition(percentile, db_path=None):
    very_low = float(get_setting("very_low_percentile", db_path, default="5"))
    low = float(get_setting("low_percentile", db_path, default="10"))
    high = float(get_setting("high_percentile", db_path, default="90"))
    very_high = float(get_setting("very_high_percentile", db_path, default="95"))

    if percentile is None:
        return "UNKNOWN"
    if percentile <= very_low:
        return "SEVERE LOW"
    if percentile <= low:
        return "LOW"
    if percentile >= very_high:
        return "SEVERE HIGH"
    if percentile >= high:
        return "HIGH"
    return "NORMAL"


def fetch_and_evaluate_site(site, db_path=None):
    """
    Fetch USGS data for one site, compute percentile and severity,
    record the condition, and return a transition dict or None.
    """
    site_id = site["id"]
    site_number = site["site_number"]
    param_code = site["parameter_code"]

    try:
        # Current value — last 7 days of interval data
        end = datetime.now()
        start = end - timedelta(days=7)
        df_iv, _ = nwis.get_iv(
            sites=site_number,
            parameterCd=param_code,
            start=start.strftime('%Y-%m-%d'),
            end=end.strftime('%Y-%m-%d')
        )
        if df_iv is None or len(df_iv) == 0:
            logger.warning("No interval data for site %s", site_number)
            return None

        param_cols = [c for c in df_iv.columns if c.startswith(param_code)]
        if not param_cols:
            return None
        current_value = pd.to_numeric(df_iv[param_cols[0]].iloc[-1], errors='coerce')
        if pd.isna(current_value) or current_value < 0:
            return None

        # Historical daily values
        start_year = get_setting("historical_start_year", db_path, default="1980")
        df_dv, _ = nwis.get_dv(
            sites=site_number,
            parameterCd=param_code,
            start=f"{start_year}-01-01",
            end=end.strftime('%Y-%m-%d')
        )
        if df_dv is None or len(df_dv) == 0:
            return None

        hist_cols = [c for c in df_dv.columns if param_code in c]
        if not hist_cols:
            return None
        hist_values = pd.to_numeric(df_dv[hist_cols[0]], errors='coerce').values
        hist_values = hist_values[~np.isnan(hist_values) & (hist_values >= 0)]
        if len(hist_values) == 0:
            return None

        percentile = float((hist_values < current_value).sum() / len(hist_values) * 100)
        unit = {"00060": "cfs", "00065": "ft"}.get(param_code, "units")
        severity = classify_condition(percentile, db_path)

        previous_severity = get_previous_severity(site_id, db_path)
        record_condition(site_id, float(current_value), unit, percentile, severity, db_path)

        transition = detect_transition(previous_severity, severity)
        if transition:
            return {
                "site_id": site_id,
                "site_number": site_number,
                "station_name": site["station_name"],
                "previous_severity": transition[0],
                "new_severity": transition[1],
                "current_value": float(current_value),
                "unit": unit,
                "percentile": percentile,
            }
        return None

    except Exception:
        logger.exception("Error evaluating site %s", site_number)
        return None


class PollingThread(threading.Thread):
    def __init__(self, notification_queue, db_path=None, stop_event=None):
        super().__init__(name="PollingThread", daemon=True)
        self.notification_queue = notification_queue
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()

    def run(self):
        logger.info("PollingThread started")
        while not self.stop_event.is_set():
            self._poll()
            interval = int(get_setting("poll_interval_minutes", self.db_path, default="15"))
            self.stop_event.wait(timeout=interval * 60)
        logger.info("PollingThread stopped")

    def _poll(self):
        sites = get_active_sites(self.db_path)
        for site in sites:
            transition = fetch_and_evaluate_site(site, self.db_path)
            if transition:
                self.notification_queue.put({
                    "type": "transition",
                    "data": transition,
                })
