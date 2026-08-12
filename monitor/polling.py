"""USGS polling loop that classifies gauge readings into severity levels."""

import threading
import time
import queue
import logging
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import dataretrieval.nwis as nwis

from db.models import (get_db, get_setting, record_site_fetch_success,
                       record_site_fetch_error)
from monitor.trend import compute_trend, exceeds_trend_threshold, trend_alert_due

logger = logging.getLogger(__name__)


def get_active_sites(db_path=None):
    """Return a list of active sites as dicts of id, site_number, station_name, and parameter_code."""
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, site_number, station_name, parameter_code FROM sites WHERE active=1"
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def get_previous_severity(site_id, db_path=None):
    """Return the most recently recorded severity for a site, or None if none exists."""
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT severity FROM site_conditions WHERE site_id=%s ORDER BY id DESC LIMIT 1",
            (site_id,)
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return row["severity"] if row else None


def get_previous_reading(site_id, db_path=None):
    """Return the most recently recorded value for a site, or None if none exists."""
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT current_value FROM site_conditions WHERE site_id=%s ORDER BY id DESC LIMIT 1",
            (site_id,)
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if row is None or row["current_value"] is None:
        return None
    return float(row["current_value"])


def record_condition(site_id, current_value, unit, percentile, severity, db_path=None):
    """Insert a new site_conditions row capturing the current reading and its severity."""
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO site_conditions
               (site_id, current_value, unit, percentile, severity)
               VALUES (%s, %s, %s, %s, %s)""",
            (site_id, current_value, unit, percentile, severity)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def detect_transition(previous_severity, new_severity):
    """Returns (old, new) tuple if severity changed, else None.

    A site with no previous severity is being read for the first time; that
    is not a change anyone asked to hear about, so it returns None rather
    than announcing "None → NORMAL".
    """
    if previous_severity is None:
        return None
    if previous_severity == new_severity:
        return None
    return (previous_severity, new_severity)


def classify_condition(percentile, db_path=None):
    """Map a percentile to a severity label using the configured thresholds.

    Returns "UNKNOWN" for None; "SEVERE LOW" at or below very_low_percentile,
    "LOW" at or below low_percentile, "SEVERE HIGH" at or above very_high_percentile,
    "HIGH" at or above high_percentile, otherwise "NORMAL".
    """
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


def _fetch_failed(site_id, message, db_path=None):
    """Log why a site produced no usable reading and record it against the site.

    Every dead-end in fetch_and_evaluate_site goes through here so a gauge
    that has silently returned nothing for days shows up in the portal and
    the logs instead of vanishing.
    """
    logger.warning("Site %s produced no reading: %s", site_id, message)
    try:
        record_site_fetch_error(site_id, message, db_path)
    except Exception:
        logger.exception("Could not record fetch error for site %s", site_id)
    return []


def _direction_from(previous_value, current_value):
    """Return RISING / FALLING / STEADY comparing the new reading to the last one."""
    if previous_value is None or float(previous_value) == float(current_value):
        return "STEADY"
    return "RISING" if float(current_value) > float(previous_value) else "FALLING"


def fetch_and_evaluate_site(site, db_path=None):
    """
    Fetch USGS data for one site, compute percentile and severity, record the
    condition, and return a list of notification messages (possibly empty).

    Each message is a dict of {"type": "transition"|"trend", "data": {...}}
    ready to be put on the notification queue.
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
            return _fetch_failed(
                site_id,
                f"USGS returned no interval data for site {site_number} "
                f"(parameter {param_code}) in the last 7 days",
                db_path)

        param_cols = [c for c in df_iv.columns if c.startswith(param_code)]
        if not param_cols:
            return _fetch_failed(
                site_id,
                f"USGS interval data for site {site_number} has no column for "
                f"parameter {param_code}; this gauge may not report it",
                db_path)
        current_value = pd.to_numeric(df_iv[param_cols[0]].iloc[-1], errors='coerce')
        if pd.isna(current_value) or current_value < 0:
            return _fetch_failed(
                site_id,
                f"Latest USGS reading for site {site_number} is not a usable "
                f"number: {df_iv[param_cols[0]].iloc[-1]!r}",
                db_path)

        # Historical daily values
        start_year = get_setting("historical_start_year", db_path, default="1980")
        df_dv, _ = nwis.get_dv(
            sites=site_number,
            parameterCd=param_code,
            start=f"{start_year}-01-01",
            end=end.strftime('%Y-%m-%d')
        )
        if df_dv is None or len(df_dv) == 0:
            return _fetch_failed(
                site_id,
                f"USGS returned no daily history for site {site_number} since "
                f"{start_year}; percentiles cannot be computed",
                db_path)

        hist_cols = [c for c in df_dv.columns if param_code in c]
        if not hist_cols:
            return _fetch_failed(
                site_id,
                f"USGS daily history for site {site_number} has no column for "
                f"parameter {param_code}",
                db_path)
        hist_values = pd.to_numeric(df_dv[hist_cols[0]], errors='coerce').values
        hist_values = hist_values[~np.isnan(hist_values) & (hist_values >= 0)]
        if len(hist_values) == 0:
            return _fetch_failed(
                site_id,
                f"USGS daily history for site {site_number} contains no usable "
                f"values; percentiles cannot be computed",
                db_path)

        percentile = float((hist_values < current_value).sum() / len(hist_values) * 100)
        unit = {"00060": "cfs", "00065": "ft"}.get(param_code, "units")
        severity = classify_condition(percentile, db_path)

        previous_severity = get_previous_severity(site_id, db_path)
        previous_value = get_previous_reading(site_id, db_path)
        record_condition(site_id, float(current_value), unit, percentile, severity, db_path)
        record_site_fetch_success(site_id, db_path)

        messages = []

        transition = detect_transition(previous_severity, severity)
        if transition:
            messages.append({
                "type": "transition",
                "data": {
                    "site_id": site_id,
                    "site_number": site_number,
                    "station_name": site["station_name"],
                    "previous_severity": transition[0],
                    "new_severity": transition[1],
                    "current_value": float(current_value),
                    "unit": unit,
                    "percentile": percentile,
                    "direction": _direction_from(previous_value, current_value),
                },
            })

        trend_message = evaluate_trend(site, unit, db_path)
        if trend_message:
            messages.append(trend_message)

        return messages

    except Exception as exc:
        logger.exception("Error evaluating site %s", site_number)
        try:
            record_site_fetch_error(site_id, str(exc) or exc.__class__.__name__, db_path)
        except Exception:
            logger.exception("Could not record fetch error for site %s", site_id)
        return []


def evaluate_trend(site, unit, db_path=None):
    """Return a "trend" queue message when this site is moving fast, else None.

    Reads the configured window, compares the change against the rise/fall
    threshold for the site's parameter, and honours the minimum interval
    between trend alerts so a long event does not repeat every poll.
    """
    window_hours = float(get_setting("rate_change_window_hours", db_path, default="6"))
    trend = compute_trend(site["id"], window_hours, db_path)
    if not trend or trend["direction"] == "STEADY":
        return None
    if not exceeds_trend_threshold(trend["delta"], site["parameter_code"],
                                   trend["start_value"], db_path):
        return None
    if not trend_alert_due(site["id"], db_path):
        return None

    return {
        "type": "trend",
        "data": {
            "site_id": site["id"],
            "site_number": site["site_number"],
            "station_name": site["station_name"],
            "unit": unit,
            **trend,
        },
    }


class PollingThread(threading.Thread):
    """Daemon thread that polls USGS for active sites on a configurable interval."""

    DEFAULT_INTERVAL_MINUTES = 15

    def __init__(self, notification_queue, db_path=None, stop_event=None):
        """Store the notification queue, optional db_path, and stop event."""
        super().__init__(name="PollingThread", daemon=True)
        self.notification_queue = notification_queue
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()

    def run(self):
        """Poll on each iteration then wait poll_interval_minutes, looping until stop_event is set.

        Any failure in a cycle — a database blip, an unreachable API — is
        logged and retried on the next interval rather than killing the
        thread and leaving the container silently half-dead.
        """
        logger.info("PollingThread started")
        while not self.stop_event.is_set():
            interval = self.DEFAULT_INTERVAL_MINUTES
            try:
                self._poll()
                interval = int(get_setting("poll_interval_minutes", self.db_path, default="15"))
            except Exception:
                logger.exception("Polling cycle failed — retrying next interval")
            self.stop_event.wait(timeout=interval * 60)
        logger.info("PollingThread stopped")

    def _poll(self):
        """Evaluate every active site once and enqueue each message it produced."""
        sites = get_active_sites(self.db_path)
        for site in sites:
            for message in fetch_and_evaluate_site(site, self.db_path):
                self.notification_queue.put(message)
