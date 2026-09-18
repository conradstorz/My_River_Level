"""
NOAA gauge polling thread.

Periodically fetches the current stage for each configured NOAA gauge,
classifies its flood condition, persists the result, and enqueues a
notification whenever a gauge's flood category changes.
"""

import threading
import logging

from db.models import (get_setting, get_all_noaa_gauges, update_noaa_gauge_condition,
                       record_noaa_observation)
from monitor.noaa_client import fetch_current_stage, classify_noaa_condition

logger = logging.getLogger(__name__)


def fetch_and_evaluate_noaa_gauge(gauge, db_path=None):
    """
    Fetch current stage for one NOAA gauge, classify condition, update DB.
    Returns a transition dict if severity changed, else None.
    """
    lid = gauge["lid"]
    stage = fetch_current_stage(lid)
    if stage is None:
        logger.warning("NOAA gauge %s returned no current stage", lid)
        return None

    # Archive every reading, transition or not, so forecasts can later be
    # scored against what actually happened.
    record_noaa_observation(lid, stage, db_path=db_path)

    new_severity = classify_noaa_condition(
        stage,
        gauge["action_stage"],
        gauge["minor_flood_stage"],
        gauge["moderate_flood_stage"],
        gauge["major_flood_stage"],
    )
    previous_severity = gauge["severity"]
    update_noaa_gauge_condition(lid, stage, new_severity, db_path)

    if new_severity != previous_severity:
        return {
            "gauge_id": gauge["id"],
            "lid": lid,
            "station_name": gauge["station_name"],
            "previous_severity": previous_severity,
            "new_severity": new_severity,
            "current_stage": stage,
        }
    return None


class NoaaPollingThread(threading.Thread):
    """
    Daemon thread that polls NOAA gauges on a configurable interval.

    On each pass it evaluates every configured gauge and pushes a
    "noaa_transition" message onto the notification queue whenever a
    gauge's flood category changes. Runs until its stop event is set.
    """

    DEFAULT_INTERVAL_MINUTES = 15

    def __init__(self, notification_queue, db_path=None, stop_event=None):
        """Store the notification queue, DB path, and stop event."""
        super().__init__(name="NoaaPollingThread", daemon=True)
        self.notification_queue = notification_queue
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()

    def run(self):
        """Poll all gauges, then sleep for poll_interval_minutes, until stopped.

        A failed cycle is logged and retried on the next interval rather than
        ending the thread and leaving the container looking healthy while it
        quietly monitors nothing.
        """
        logger.info("NoaaPollingThread started")
        while not self.stop_event.is_set():
            interval = self.DEFAULT_INTERVAL_MINUTES
            try:
                self._poll()
                interval = int(get_setting("poll_interval_minutes", self.db_path, default="15"))
            except Exception:
                logger.exception("NOAA polling cycle failed — retrying next interval")
            self.stop_event.wait(timeout=interval * 60)
        logger.info("NoaaPollingThread stopped")

    def _poll(self):
        """Evaluate every gauge once, enqueuing a transition on category change."""
        gauges = get_all_noaa_gauges(self.db_path, active_only=True)
        for gauge in gauges:
            try:
                transition = fetch_and_evaluate_noaa_gauge(gauge, self.db_path)
                if transition:
                    self.notification_queue.put({
                        "type": "noaa_transition",
                        "data": transition,
                    })
            except Exception:
                logger.exception("Error polling NOAA gauge %s", gauge["lid"])
