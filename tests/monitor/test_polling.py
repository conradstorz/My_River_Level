import threading
import time

import pandas as pd
import pytest
from unittest.mock import patch, MagicMock
from db.models import init_db, get_db, get_sites_with_health, set_setting
from monitor.polling import (detect_transition, record_condition, get_active_sites,
                             get_previous_reading, fetch_and_evaluate_site,
                             PollingThread)

def test_detect_transition_returns_none_when_same_severity():
    assert detect_transition("HIGH", "HIGH") is None

def test_detect_transition_returns_tuple_when_different():
    result = detect_transition("NORMAL", "HIGH")
    assert result == ("NORMAL", "HIGH")

def test_detect_transition_normal_to_severe():
    result = detect_transition("NORMAL", "SEVERE HIGH")
    assert result == ("NORMAL", "SEVERE HIGH")

def test_detect_transition_returns_none_on_first_ever_reading():
    """A site's first poll has no previous severity, so it is not a transition.

    Sending "Condition changed: None → NORMAL" to every subscriber the first
    time a site is polled is noise, not news.
    """
    assert detect_transition(None, "NORMAL") is None
    assert detect_transition(None, "HIGH") is None

def test_record_condition_inserts_row(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number) VALUES ('12345678')")
    conn.commit()
    cur.execute("SELECT id FROM sites WHERE site_number='12345678'")
    site_id = cur.fetchone()["id"]
    cur.close()
    conn.close()

    record_condition(site_id, 500.0, "cfs", 45.2, "NORMAL", tmp_db)

    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT * FROM site_conditions WHERE site_id=%s", (site_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    assert row["current_value"] == 500.0
    assert row["severity"] == "NORMAL"
    assert row["percentile"] == pytest.approx(45.2)

def test_get_active_sites_returns_only_active(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, active) VALUES ('11111111', 1)")
    cur.execute("INSERT INTO sites (site_number, active) VALUES ('22222222', 0)")
    conn.commit()
    cur.close()
    conn.close()

    sites = get_active_sites(tmp_db)
    site_numbers = [s["site_number"] for s in sites]
    assert "11111111" in site_numbers
    assert "22222222" not in site_numbers


# --- helpers ---------------------------------------------------------------

def _make_site(tmp_db, site_number="12345678", parameter_code="00065"):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sites (site_number, station_name, parameter_code) "
        "VALUES (%s, 'Test River', %s) RETURNING id",
        (site_number, parameter_code),
    )
    site_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return {"id": site_id, "site_number": site_number,
            "station_name": "Test River", "parameter_code": parameter_code}


def _iv_frame(value, param_code="00065"):
    return pd.DataFrame({param_code: [value - 1, value]})


def _dv_frame(values, param_code="00065"):
    return pd.DataFrame({f"{param_code}_Mean": values})


# --- get_previous_reading --------------------------------------------------

def test_get_previous_reading_returns_none_without_history(tmp_db):
    site = _make_site(tmp_db)
    assert get_previous_reading(site["id"], tmp_db) is None


def test_get_previous_reading_returns_latest_value(tmp_db):
    site = _make_site(tmp_db)
    record_condition(site["id"], 10.0, "ft", 50.0, "NORMAL", tmp_db)
    record_condition(site["id"], 12.5, "ft", 60.0, "NORMAL", tmp_db)
    assert get_previous_reading(site["id"], tmp_db) == 12.5


# --- fetch_and_evaluate_site ----------------------------------------------

def test_first_poll_produces_no_messages(tmp_db):
    """A brand-new site must not emit a None → NORMAL alert on its first poll."""
    site = _make_site(tmp_db)
    with patch("monitor.polling.nwis") as mock_nwis:
        mock_nwis.get_iv.return_value = (_iv_frame(10.0), None)
        mock_nwis.get_dv.return_value = (_dv_frame([5.0, 6.0, 7.0, 8.0]), None)
        messages = fetch_and_evaluate_site(site, tmp_db)
    assert messages == []


def test_transition_message_includes_direction(tmp_db):
    site = _make_site(tmp_db)
    # Seed a previous NORMAL reading below the new one.
    record_condition(site["id"], 6.0, "ft", 50.0, "NORMAL", tmp_db)
    with patch("monitor.polling.nwis") as mock_nwis:
        mock_nwis.get_iv.return_value = (_iv_frame(100.0), None)
        mock_nwis.get_dv.return_value = (_dv_frame([1.0, 2.0, 3.0, 4.0]), None)
        messages = fetch_and_evaluate_site(site, tmp_db)
    transitions = [m for m in messages if m["type"] == "transition"]
    assert len(transitions) == 1
    data = transitions[0]["data"]
    assert data["previous_severity"] == "NORMAL"
    assert data["new_severity"] == "SEVERE HIGH"
    assert data["direction"] == "RISING"


def test_direction_is_falling_when_value_drops(tmp_db):
    site = _make_site(tmp_db)
    record_condition(site["id"], 500.0, "ft", 99.0, "SEVERE HIGH", tmp_db)
    with patch("monitor.polling.nwis") as mock_nwis:
        mock_nwis.get_iv.return_value = (_iv_frame(2.0), None)
        mock_nwis.get_dv.return_value = (_dv_frame([10.0, 20.0, 30.0, 40.0]), None)
        messages = fetch_and_evaluate_site(site, tmp_db)
    transitions = [m for m in messages if m["type"] == "transition"]
    assert transitions[0]["data"]["direction"] == "FALLING"


def test_successful_evaluation_records_site_health(tmp_db):
    site = _make_site(tmp_db)
    with patch("monitor.polling.nwis") as mock_nwis:
        mock_nwis.get_iv.return_value = (_iv_frame(10.0), None)
        mock_nwis.get_dv.return_value = (_dv_frame([5.0, 6.0, 7.0]), None)
        fetch_and_evaluate_site(site, tmp_db)
    row = [r for r in get_sites_with_health(tmp_db) if r["id"] == site["id"]][0]
    assert row["stale"] is False
    assert row["last_error"] == ""


def test_missing_parameter_column_records_an_error(tmp_db):
    """A site whose data lacks the requested parameter must not fail silently."""
    site = _make_site(tmp_db)
    with patch("monitor.polling.nwis") as mock_nwis:
        mock_nwis.get_iv.return_value = (pd.DataFrame({"99999": [1.0]}), None)
        mock_nwis.get_dv.return_value = (_dv_frame([5.0]), None)
        messages = fetch_and_evaluate_site(site, tmp_db)
    assert messages == []
    row = [r for r in get_sites_with_health(tmp_db) if r["id"] == site["id"]][0]
    assert row["stale"] is True
    assert "00065" in row["last_error"]


def test_empty_interval_data_records_an_error(tmp_db):
    site = _make_site(tmp_db)
    with patch("monitor.polling.nwis") as mock_nwis:
        mock_nwis.get_iv.return_value = (pd.DataFrame(), None)
        mock_nwis.get_dv.return_value = (_dv_frame([5.0]), None)
        messages = fetch_and_evaluate_site(site, tmp_db)
    assert messages == []
    row = [r for r in get_sites_with_health(tmp_db) if r["id"] == site["id"]][0]
    assert row["last_error"] != ""


def test_empty_history_records_an_error(tmp_db):
    site = _make_site(tmp_db)
    with patch("monitor.polling.nwis") as mock_nwis:
        mock_nwis.get_iv.return_value = (_iv_frame(10.0), None)
        mock_nwis.get_dv.return_value = (pd.DataFrame(), None)
        messages = fetch_and_evaluate_site(site, tmp_db)
    assert messages == []
    row = [r for r in get_sites_with_health(tmp_db) if r["id"] == site["id"]][0]
    assert row["last_error"] != ""


def test_fetch_exception_records_the_error(tmp_db):
    site = _make_site(tmp_db)
    with patch("monitor.polling.nwis") as mock_nwis:
        mock_nwis.get_iv.side_effect = RuntimeError("USGS is down")
        messages = fetch_and_evaluate_site(site, tmp_db)
    assert messages == []
    row = [r for r in get_sites_with_health(tmp_db) if r["id"] == site["id"]][0]
    assert "USGS is down" in row["last_error"]


def test_rapid_rise_emits_a_trend_message_without_a_band_crossing(tmp_db):
    """The headline gap: a fast-rising river that never leaves the NORMAL band."""
    set_setting("rate_change_threshold_ft", "2.0", tmp_db)
    set_setting("rate_change_window_hours", "6", tmp_db)
    site = _make_site(tmp_db)
    # Two earlier NORMAL readings inside the window; the new one rises 4 ft.
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO site_conditions
           (site_id, current_value, unit, percentile, severity, checked_at)
           VALUES (%s, 6.0, 'ft', 50, 'NORMAL', (NOW() - INTERVAL '4 hours')::TEXT)""",
        (site["id"],),
    )
    conn.commit()
    cur.close()
    conn.close()

    with patch("monitor.polling.nwis") as mock_nwis:
        mock_nwis.get_iv.return_value = (_iv_frame(10.0), None)
        # History spans the new value so the percentile stays mid-band.
        mock_nwis.get_dv.return_value = (_dv_frame([1.0, 5.0, 9.0, 20.0, 30.0]), None)
        messages = fetch_and_evaluate_site(site, tmp_db)

    assert [m["type"] for m in messages] == ["trend"]
    data = messages[0]["data"]
    assert data["direction"] == "RISING"
    assert data["delta"] == pytest.approx(4.0)
    assert data["end_value"] == pytest.approx(10.0)
    assert data["start_value"] == pytest.approx(6.0)
    assert data["unit"] == "ft"
    assert data["site_id"] == site["id"]


def test_small_change_emits_no_trend_message(tmp_db):
    set_setting("rate_change_threshold_ft", "2.0", tmp_db)
    site = _make_site(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO site_conditions
           (site_id, current_value, unit, percentile, severity, checked_at)
           VALUES (%s, 9.5, 'ft', 50, 'NORMAL', (NOW() - INTERVAL '4 hours')::TEXT)""",
        (site["id"],),
    )
    conn.commit()
    cur.close()
    conn.close()

    with patch("monitor.polling.nwis") as mock_nwis:
        mock_nwis.get_iv.return_value = (_iv_frame(10.0), None)
        mock_nwis.get_dv.return_value = (_dv_frame([1.0, 5.0, 9.0, 20.0, 30.0]), None)
        messages = fetch_and_evaluate_site(site, tmp_db)
    assert messages == []


def test_trend_message_suppressed_when_one_was_just_sent(tmp_db):
    set_setting("rate_change_threshold_ft", "2.0", tmp_db)
    set_setting("rate_change_min_interval_hours", "6", tmp_db)
    site = _make_site(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO site_conditions
           (site_id, current_value, unit, percentile, severity, checked_at)
           VALUES (%s, 6.0, 'ft', 50, 'NORMAL', (NOW() - INTERVAL '4 hours')::TEXT)""",
        (site["id"],),
    )
    cur.execute(
        """INSERT INTO notifications (site_id, channel, message_text, trigger_type, success)
           VALUES (%s, 'telegram', 'msg', 'trend', 1)""",
        (site["id"],),
    )
    conn.commit()
    cur.close()
    conn.close()

    with patch("monitor.polling.nwis") as mock_nwis:
        mock_nwis.get_iv.return_value = (_iv_frame(10.0), None)
        mock_nwis.get_dv.return_value = (_dv_frame([1.0, 5.0, 9.0, 20.0, 30.0]), None)
        messages = fetch_and_evaluate_site(site, tmp_db)
    assert messages == []


# --- PollingThread ---------------------------------------------------------

def test_poll_enqueues_every_message_returned_for_a_site(tmp_db):
    import queue as queue_mod
    site = _make_site(tmp_db)
    q = queue_mod.Queue()
    thread = PollingThread(q, db_path=tmp_db)
    fake = [
        {"type": "transition", "data": {"site_id": site["id"]}},
        {"type": "trend", "data": {"site_id": site["id"]}},
    ]
    with patch("monitor.polling.fetch_and_evaluate_site", return_value=fake):
        thread._poll()
    assert [q.get()["type"] for _ in range(2)] == ["transition", "trend"]


def test_polling_thread_survives_a_failing_poll(tmp_db):
    """One Postgres blip must not kill polling for the life of the container."""
    calls = []
    stop_event = threading.Event()
    thread = PollingThread(MagicMock(), db_path=tmp_db, stop_event=stop_event)

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database went away")

    thread._poll = flaky
    thread.DEFAULT_INTERVAL_MINUTES = 0
    with patch("monitor.polling.get_setting", return_value="0"):
        thread.start()
        deadline = time.time() + 5
        while len(calls) < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert thread.is_alive()
        assert len(calls) >= 2
        stop_event.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
