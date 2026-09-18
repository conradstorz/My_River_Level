"""
NOAA forecast archiving thread.

Every `forecast_poll_hours` this thread fetches each NOAA gauge's published
forecast, archives the points, and re-grades every gauge against the levels
that were actually observed (see `monitor.gauge_quality`). Archiving is what
makes the grade possible at all: the accuracy of a forecast can only be judged
after the moment it described has passed.
"""

import logging
import threading

from db.models import (get_all_noaa_gauges, get_setting, record_forecast_points,
                       set_gauge_forecast_availability)
from monitor.gauge_quality import score_all_gauges
from monitor.noaa_client import fetch_forecast_result

logger = logging.getLogger(__name__)


class ForecastPollingThread(threading.Thread):
    """
    Daemon thread that archives NOAA forecasts and grades each gauge.

    Runs until its stop event is set. The loop body is wrapped so that a
    failed pass — a Postgres blip, an NWPS outage — is logged and retried on
    the next interval instead of silently killing the thread.
    """

    DEFAULT_INTERVAL_HOURS = 6

    def __init__(self, db_path=None, stop_event=None):
        """Store the DB path and stop event; no notification queue needed."""
        super().__init__(name="ForecastPollingThread", daemon=True)
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()

    def run(self):
        """Archive and grade once per pass, then wait forecast_poll_hours."""
        logger.info("ForecastPollingThread started")
        while not self.stop_event.is_set():
            interval = self.DEFAULT_INTERVAL_HOURS
            try:
                self._poll()
                interval = float(get_setting("forecast_poll_hours",
                                             self.db_path, default="6"))
            except Exception:
                logger.exception("Forecast cycle failed — retrying next interval")
            self.stop_event.wait(timeout=interval * 3600)
        logger.info("ForecastPollingThread stopped")

    def _poll(self):
        """Fetch and archive every gauge's forecast, then re-grade them all."""
        gauges = get_all_noaa_gauges(self.db_path, active_only=True)
        for gauge in gauges:
            lid = gauge["lid"]
            try:
                result = fetch_forecast_result(lid)
                # Only a definite answer updates has_forecast. On "error" we
                # leave it NULL so the grade stays "not yet assessed" instead
                # of claiming NOAA publishes no forecast for this gauge.
                if result["status"] in ("ok", "none"):
                    set_gauge_forecast_availability(
                        lid, result["status"] == "ok", self.db_path)
                forecast = result["forecast"]
                if not forecast:
                    continue
                stored = record_forecast_points(lid, forecast["issued_at"],
                                                forecast["points"], self.db_path)
                logger.info("Archived %s new forecast point(s) for %s",
                            stored, lid)
            except Exception:
                logger.exception("Error archiving NOAA forecast for %s", lid)
        score_all_gauges(self.db_path)
