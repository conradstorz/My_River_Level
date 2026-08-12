"""Tests for the forecast-archiving thread."""

import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from db.models import (get_forecast_points, get_gauge_quality,
                       get_or_create_noaa_gauge)
from monitor.forecast_polling import ForecastPollingThread


def _issued():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _payload(issued, stage=15.0, hours=24):
    return {"issued_at": issued,
            "points": [{"valid_at": issued + timedelta(hours=hours),
                        "stage": stage}]}


def test_poll_archives_forecast_points_and_scores_the_gauge(tmp_db):
    get_or_create_noaa_gauge("abcd1", "Test", 10.0, 12.0, 14.0, 16.0, tmp_db)
    issued = _issued()
    with patch("monitor.forecast_polling.fetch_forecast_result",
               return_value={"status": "ok", "forecast": _payload(issued)}) as mock_fetch:
        ForecastPollingThread(db_path=tmp_db)._poll()

    assert mock_fetch.call_args[0][0] == "abcd1"
    points = get_forecast_points("abcd1", tmp_db)
    assert len(points) == 1
    assert points[0]["predicted_stage"] == 15.0
    quality = get_gauge_quality("abcd1", tmp_db)
    assert quality is not None
    assert "Not reporting" in quality["detail"]  # no observations recorded yet


def test_poll_handles_a_gauge_noaa_confirms_has_no_forecast(tmp_db):
    """A definite 'none' from NOAA is what earns the Observation-only grade."""
    get_or_create_noaa_gauge("abcd1", "Test", 10.0, 12.0, 14.0, 16.0, tmp_db)
    with patch("monitor.forecast_polling.fetch_forecast_result",
               return_value={"status": "none", "forecast": None}):
        ForecastPollingThread(db_path=tmp_db)._poll()

    assert get_forecast_points("abcd1", tmp_db) == []
    quality = get_gauge_quality("abcd1", tmp_db)
    assert quality["grade"] == "D"
    assert "Observation only" in quality["detail"]


def test_poll_does_not_blame_the_gauge_when_the_fetch_errors(tmp_db):
    """An NWPS outage must leave has_forecast NULL, not assert 'no forecast'."""
    from db.models import get_all_noaa_gauges
    get_or_create_noaa_gauge("abcd1", "Test", 10.0, 12.0, 14.0, 16.0, tmp_db)
    with patch("monitor.forecast_polling.fetch_forecast_result",
               return_value={"status": "error", "forecast": None}):
        ForecastPollingThread(db_path=tmp_db)._poll()

    gauge = [g for g in get_all_noaa_gauges(tmp_db) if g["lid"] == "abcd1"][0]
    assert gauge["has_forecast"] is None
    quality = get_gauge_quality("abcd1", tmp_db)
    assert quality["grade"] == "Unrated"
    assert "Observation only" not in quality["detail"]


def test_poll_continues_after_one_gauge_raises(tmp_db):
    get_or_create_noaa_gauge("aaaa1", "First", 10.0, 12.0, 14.0, 16.0, tmp_db)
    get_or_create_noaa_gauge("bbbb2", "Second", 10.0, 12.0, 14.0, 16.0, tmp_db)
    issued = _issued()

    def flaky(lid):
        if lid == "aaaa1":
            raise RuntimeError("NWPS exploded")
        return {"status": "ok", "forecast": _payload(issued)}

    with patch("monitor.forecast_polling.fetch_forecast_result", side_effect=flaky):
        ForecastPollingThread(db_path=tmp_db)._poll()

    assert get_forecast_points("aaaa1", tmp_db) == []
    assert len(get_forecast_points("bbbb2", tmp_db)) == 1


def test_run_survives_a_failing_poll_and_keeps_looping(tmp_db):
    calls = []
    thread = ForecastPollingThread(db_path=tmp_db)
    thread.DEFAULT_INTERVAL_HOURS = 0.0001

    def boom():
        calls.append(1)
        raise RuntimeError("database went away")

    thread._poll = boom
    thread.start()
    for _ in range(200):
        if len(calls) >= 2:
            break
        time.sleep(0.01)
    thread.stop_event.set()
    thread.join(timeout=5)

    assert len(calls) >= 2, "loop stopped after the first failure"
    assert not thread.is_alive()


def test_run_stops_when_the_stop_event_is_set(tmp_db):
    stop_event = threading.Event()
    thread = ForecastPollingThread(db_path=tmp_db, stop_event=stop_event)
    thread._poll = lambda: None
    thread.start()
    time.sleep(0.05)
    stop_event.set()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_thread_defaults(tmp_db):
    thread = ForecastPollingThread(db_path=tmp_db)
    assert thread.name == "ForecastPollingThread"
    assert thread.daemon is True
    assert thread.DEFAULT_INTERVAL_HOURS == 6
