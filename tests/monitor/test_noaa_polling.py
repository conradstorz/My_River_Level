import queue
from unittest.mock import patch
from db.models import init_db, get_or_create_noaa_gauge
from monitor.noaa_polling import fetch_and_evaluate_noaa_gauge, NoaaPollingThread


def _gauge(severity="Normal"):
    return {
        "id": 1, "lid": "MLUK2", "station_name": "Ohio River at McAlpine Upper",
        "action_stage": 21.0, "minor_flood_stage": 23.0,
        "moderate_flood_stage": 30.0, "major_flood_stage": 38.0,
        "severity": severity,
    }


def test_fetch_no_change(tmp_db):
    init_db(tmp_db)
    get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, tmp_db)
    with patch("monitor.noaa_polling.fetch_current_stage", return_value=15.0):
        result = fetch_and_evaluate_noaa_gauge(_gauge("Normal"), tmp_db)
    assert result is None   # No transition


def test_fetch_transition(tmp_db):
    init_db(tmp_db)
    get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, tmp_db)
    with patch("monitor.noaa_polling.fetch_current_stage", return_value=22.0):
        result = fetch_and_evaluate_noaa_gauge(_gauge("Normal"), tmp_db)
    assert result is not None
    assert result["previous_severity"] == "Normal"
    assert result["new_severity"] == "Action"
    assert result["current_stage"] == 22.0


def test_fetch_no_stage_returns_none(tmp_db):
    init_db(tmp_db)
    get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, tmp_db)
    with patch("monitor.noaa_polling.fetch_current_stage", return_value=None):
        result = fetch_and_evaluate_noaa_gauge(_gauge("Normal"), tmp_db)
    assert result is None


def test_polling_thread_enqueues(tmp_db):
    init_db(tmp_db)
    get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, tmp_db)
    q = queue.Queue()
    thread = NoaaPollingThread(q, db_path=tmp_db)
    with patch("monitor.noaa_polling.fetch_current_stage", return_value=22.0):
        thread._poll()
    assert not q.empty()
    item = q.get()
    assert item["type"] == "noaa_transition"
    assert item["data"]["lid"] == "MLUK2"
    assert item["data"]["new_severity"] == "Action"


def test_polling_thread_no_enqueue_on_no_change(tmp_db):
    init_db(tmp_db)
    get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, tmp_db)
    q = queue.Queue()
    thread = NoaaPollingThread(q, db_path=tmp_db)
    # Stage is 15ft — Normal, same as the default severity in the DB
    with patch("monitor.noaa_polling.fetch_current_stage", return_value=15.0):
        thread._poll()
    assert q.empty()
