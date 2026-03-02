import threading
import logging
from datetime import datetime, timedelta, timezone

from db.models import get_db, get_setting

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
    row = conn.execute(
        """SELECT sent_at FROM notifications
           WHERE site_id = ? AND trigger_type = 'reminder'
           ORDER BY sent_at DESC LIMIT 1""",
        (site_id,)
    ).fetchone()
    conn.close()

    if row is None:
        return True

    last_sent_str = row["sent_at"]
    # SQLite datetime('now') returns UTC without timezone suffix — parse as UTC
    last_sent = datetime.fromisoformat(last_sent_str).replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last_sent >= timedelta(hours=interval_hours)


def get_current_site_severities(db_path=None):
    """Return list of {site_id, site_number, station_name, severity, current_value, unit, percentile} for all active sites."""
    conn = get_db(db_path)
    rows = conn.execute(
        """SELECT s.id AS site_id, s.site_number, s.station_name,
                  sc.severity, sc.current_value, sc.unit, sc.percentile
           FROM sites s
           JOIN site_conditions sc ON sc.id = (
               SELECT id FROM site_conditions
               WHERE site_id = s.id ORDER BY id DESC LIMIT 1
           )
           WHERE s.active = 1"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


class SchedulerThread(threading.Thread):
    CHECK_INTERVAL_SECONDS = 300  # check every 5 minutes

    def __init__(self, notification_queue, db_path=None, stop_event=None):
        super().__init__(name="SchedulerThread", daemon=True)
        self.notification_queue = notification_queue
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()

    def run(self):
        logger.info("SchedulerThread started")
        while not self.stop_event.is_set():
            self._check_reminders()
            self.stop_event.wait(timeout=self.CHECK_INTERVAL_SECONDS)
        logger.info("SchedulerThread stopped")

    def _check_reminders(self):
        try:
            for site in get_current_site_severities(self.db_path):
                if is_reminder_due(site["site_id"], site["severity"], self.db_path):
                    self.notification_queue.put({
                        "type": "reminder",
                        "data": site,
                    })
        except Exception:
            logger.exception("Error checking reminders")
