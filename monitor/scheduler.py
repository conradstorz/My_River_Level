"""Scheduler that throttles and repeats reminder alerts for persistent conditions."""

import threading
import logging
import time
from datetime import datetime, timedelta, timezone

from db.models import get_db, get_setting
from monitor.retirement import sweep

logger = logging.getLogger(__name__)


def get_reminder_interval_hours(severity, db_path=None):
    """Return reminder interval in hours for the given severity, or None if no reminder."""
    if severity in ("SEVERE LOW", "SEVERE HIGH"):
        return float(get_setting("reminder_severe_hours", db_path, default="4"))
    if severity in ("LOW", "HIGH"):
        return float(get_setting("reminder_low_high_hours", db_path, default="24"))
    return None


def is_reminder_due(site_id, severity, db_path=None):
    """Return True if a reminder should fire for this site at this severity level."""
    interval_hours = get_reminder_interval_hours(severity, db_path)
    if interval_hours is None:
        return False

    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT sent_at FROM notifications
               WHERE site_id = %s AND trigger_type = 'reminder'
               ORDER BY sent_at DESC LIMIT 1""",
            (site_id,)
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if row is None:
        return True

    last_sent_str = row["sent_at"]
    # PostgreSQL NOW()::TEXT returns a UTC timestamp; fromisoformat() handles
    # both naive strings and offset suffixes like "+00" (Python 3.11+).
    parsed = datetime.fromisoformat(last_sent_str)
    last_sent = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last_sent >= timedelta(hours=interval_hours)


def get_current_site_severities(db_path=None):
    """Return list of {site_id, site_number, station_name, severity, current_value, unit, percentile} for all active sites."""
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT s.id AS site_id, s.site_number, s.station_name,
                      sc.severity, sc.current_value, sc.unit, sc.percentile
               FROM sites s
               JOIN site_conditions sc ON sc.id = (
                   SELECT id FROM site_conditions
                   WHERE site_id = s.id ORDER BY id DESC LIMIT 1
               )
               WHERE s.active = 1"""
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


#: Which USGS severities each sensitivity level wants to hear about. Trend
#: (rise/fall rate) alerts are gated separately because they carry no
#: severity. NOAA flood-category changes reach every level.
_SEVERITIES_FOR = {
    "floods": {"SEVERE HIGH"},
    "unusual": {"SEVERE HIGH", "HIGH", "LOW", "SEVERE LOW", "NORMAL"},
    "all": {"SEVERE HIGH", "HIGH", "LOW", "SEVERE LOW", "NORMAL"},
}


def alert_allowed(sensitivity, alert_type, severity, previous_severity=None):
    """Return True if a page at `sensitivity` should receive this alert.

    Applied at dispatch time, so two pages watching the same gauge with
    different dials still cost one poll. An unrecognised dial is treated as
    the default 'unusual' rather than silencing the page. For a `transition`,
    `previous_severity` is also checked so a 'floods' page gets the all-clear
    when a SEVERE HIGH condition drops back to NORMAL -- the new severity
    alone would otherwise not qualify.
    """
    level = sensitivity if sensitivity in _SEVERITIES_FOR else "unusual"
    if alert_type == "noaa_transition":
        return True
    if alert_type == "trend":
        return level == "all"
    if alert_type == "transition":
        return severity in _SEVERITIES_FOR[level] or previous_severity in _SEVERITIES_FOR[level]
    if alert_type == "reminder":
        return severity in _SEVERITIES_FOR[level]
    return True


class SchedulerThread(threading.Thread):
    """Daemon thread that re-enqueues reminder alerts for sites still in an alert state."""

    CHECK_INTERVAL_SECONDS = 300  # check every 5 minutes
    SWEEP_INTERVAL_SECONDS = 3600  # retire unreferenced user sources hourly

    def __init__(self, notification_queue, db_path=None, stop_event=None):
        """Store the notification queue, optional db_path, and stop event."""
        super().__init__(name="SchedulerThread", daemon=True)
        self.notification_queue = notification_queue
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()
        self._last_sweep = 0.0

    def run(self):
        """Check reminders each iteration, sweep hourly, until stop_event is set."""
        logger.info("SchedulerThread started")
        while not self.stop_event.is_set():
            self._check_reminders()
            self._maybe_sweep()
            self.stop_event.wait(timeout=self.CHECK_INTERVAL_SECONDS)
        logger.info("SchedulerThread stopped")

    def _check_reminders(self):
        """Enqueue a reminder message for each active site whose severity reminder is due."""
        try:
            for site in get_current_site_severities(self.db_path):
                if is_reminder_due(site["site_id"], site["severity"], self.db_path):
                    self.notification_queue.put({
                        "type": "reminder",
                        "data": site,
                    })
        except Exception:
            logger.exception("Error checking reminders")

    def _maybe_sweep(self):
        """Run the retirement sweep if SWEEP_INTERVAL_SECONDS have passed."""
        now = time.monotonic()
        if now - self._last_sweep < self.SWEEP_INTERVAL_SECONDS:
            return
        try:
            sweep(self.db_path)
        except Exception:
            # Leave _last_sweep alone so the next reminder pass (5 min) retries,
            # instead of leaving stale rows for another hour.
            logger.exception("Retirement sweep failed — will retry next pass")
            return
        self._last_sweep = now
