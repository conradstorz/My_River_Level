"""Tests for monitor.trend — rate-of-change detection over recent readings."""

from db.models import get_db, set_setting
from monitor.trend import compute_trend, exceeds_trend_threshold, trend_alert_due


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
    return site_id


def _add_reading(tmp_db, site_id, value, hours_ago, unit="ft"):
    """Insert a site_conditions row stamped `hours_ago` hours in the past."""
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO site_conditions
           (site_id, current_value, unit, percentile, severity, checked_at)
           VALUES (%s, %s, %s, 50, 'NORMAL', (NOW() - (%s * INTERVAL '1 hour'))::TEXT)""",
        (site_id, value, unit, hours_ago),
    )
    conn.commit()
    cur.close()
    conn.close()


def _log_trend_notification(tmp_db, site_id, hours_ago=0):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO notifications
           (subscriber_id, site_id, channel, message_text, trigger_type, success, sent_at)
           VALUES (NULL, %s, 'telegram', 'msg', 'trend', 1,
                   (NOW() - (%s * INTERVAL '1 hour'))::TEXT)""",
        (site_id, hours_ago),
    )
    conn.commit()
    cur.close()
    conn.close()


# --- compute_trend ---------------------------------------------------------

def test_compute_trend_returns_none_with_no_readings(tmp_db):
    site_id = _make_site(tmp_db)
    assert compute_trend(site_id, 6, tmp_db) is None


def test_compute_trend_returns_none_with_one_reading(tmp_db):
    site_id = _make_site(tmp_db)
    _add_reading(tmp_db, site_id, 10.0, hours_ago=1)
    assert compute_trend(site_id, 6, tmp_db) is None


def test_compute_trend_reports_rising(tmp_db):
    site_id = _make_site(tmp_db)
    _add_reading(tmp_db, site_id, 10.0, hours_ago=4)
    _add_reading(tmp_db, site_id, 13.5, hours_ago=0)
    trend = compute_trend(site_id, 6, tmp_db)
    assert trend["direction"] == "RISING"
    assert round(trend["delta"], 2) == 3.5
    assert trend["start_value"] == 10.0
    assert trend["end_value"] == 13.5
    assert 3.5 <= trend["hours"] <= 4.5


def test_compute_trend_reports_falling_with_negative_delta(tmp_db):
    site_id = _make_site(tmp_db)
    _add_reading(tmp_db, site_id, 20.0, hours_ago=3)
    _add_reading(tmp_db, site_id, 16.0, hours_ago=0)
    trend = compute_trend(site_id, 6, tmp_db)
    assert trend["direction"] == "FALLING"
    assert round(trend["delta"], 2) == -4.0


def test_compute_trend_reports_steady_when_unchanged(tmp_db):
    site_id = _make_site(tmp_db)
    _add_reading(tmp_db, site_id, 12.0, hours_ago=3)
    _add_reading(tmp_db, site_id, 12.0, hours_ago=0)
    trend = compute_trend(site_id, 6, tmp_db)
    assert trend["direction"] == "STEADY"
    assert trend["delta"] == 0.0


def test_compute_trend_ignores_readings_outside_the_window(tmp_db):
    site_id = _make_site(tmp_db)
    _add_reading(tmp_db, site_id, 2.0, hours_ago=48)
    _add_reading(tmp_db, site_id, 10.0, hours_ago=1)
    # Only one reading falls inside a 6-hour window.
    assert compute_trend(site_id, 6, tmp_db) is None


def test_compute_trend_only_sees_its_own_site(tmp_db):
    site_a = _make_site(tmp_db, "11111111")
    site_b = _make_site(tmp_db, "22222222")
    _add_reading(tmp_db, site_a, 5.0, hours_ago=3)
    _add_reading(tmp_db, site_b, 50.0, hours_ago=2)
    _add_reading(tmp_db, site_a, 9.0, hours_ago=0)
    trend = compute_trend(site_a, 6, tmp_db)
    assert trend["start_value"] == 5.0
    assert trend["end_value"] == 9.0


# --- exceeds_trend_threshold ----------------------------------------------

def test_stage_site_uses_foot_threshold(tmp_db):
    set_setting("rate_change_threshold_ft", "2.0", tmp_db)
    assert exceeds_trend_threshold(2.5, "00065", 10.0, tmp_db) is True
    assert exceeds_trend_threshold(-2.5, "00065", 10.0, tmp_db) is True
    assert exceeds_trend_threshold(1.5, "00065", 10.0, tmp_db) is False


def test_stage_threshold_is_inclusive(tmp_db):
    set_setting("rate_change_threshold_ft", "2.0", tmp_db)
    assert exceeds_trend_threshold(2.0, "00065", 10.0, tmp_db) is True


def test_discharge_site_uses_percentage_threshold(tmp_db):
    set_setting("rate_change_threshold_pct", "25", tmp_db)
    # 25% of 1000 cfs is 250 cfs
    assert exceeds_trend_threshold(300.0, "00060", 1000.0, tmp_db) is True
    assert exceeds_trend_threshold(-300.0, "00060", 1000.0, tmp_db) is True
    assert exceeds_trend_threshold(100.0, "00060", 1000.0, tmp_db) is False


def test_discharge_threshold_false_when_start_value_is_zero_or_negative(tmp_db):
    set_setting("rate_change_threshold_pct", "25", tmp_db)
    assert exceeds_trend_threshold(500.0, "00060", 0.0, tmp_db) is False
    assert exceeds_trend_threshold(500.0, "00060", -3.0, tmp_db) is False


# --- trend_alert_due -------------------------------------------------------

def test_trend_alert_due_when_never_sent(tmp_db):
    site_id = _make_site(tmp_db)
    assert trend_alert_due(site_id, tmp_db) is True


def test_trend_alert_not_due_right_after_a_trend_notification(tmp_db):
    set_setting("rate_change_min_interval_hours", "6", tmp_db)
    site_id = _make_site(tmp_db)
    _log_trend_notification(tmp_db, site_id, hours_ago=0)
    assert trend_alert_due(site_id, tmp_db) is False


def test_trend_alert_due_again_after_the_interval(tmp_db):
    set_setting("rate_change_min_interval_hours", "6", tmp_db)
    site_id = _make_site(tmp_db)
    _log_trend_notification(tmp_db, site_id, hours_ago=7)
    assert trend_alert_due(site_id, tmp_db) is True


def test_trend_alert_due_ignores_other_trigger_types(tmp_db):
    set_setting("rate_change_min_interval_hours", "6", tmp_db)
    site_id = _make_site(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO notifications (site_id, channel, message_text, trigger_type, success)
           VALUES (%s, 'telegram', 'msg', 'transition', 1)""",
        (site_id,),
    )
    conn.commit()
    cur.close()
    conn.close()
    assert trend_alert_due(site_id, tmp_db) is True


def test_trend_alert_due_reads_the_last_inserted_row_not_the_text_max(tmp_db):
    """The last-inserted row wins, even when another row's text sorts higher.

    `sent_at` is TEXT holding a rendered timestamptz, so a lexicographic
    ORDER BY mis-orders rows whose printed offset differs (e.g. across a DST
    change). Ordering by id DESC — insertion order — is the only correct
    choice. Here the newest row is old, so an alert IS due; a text sort would
    latch onto the earlier, recent row and wrongly suppress the alert.
    """
    set_setting("rate_change_min_interval_hours", "6", tmp_db)
    site_id = _make_site(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO notifications (site_id, channel, message_text, trigger_type, success, sent_at)
           VALUES (%s, 'telegram', 'sorts-high', 'trend', 1, NOW()::TEXT)""",
        (site_id,),
    )
    cur.execute(
        """INSERT INTO notifications (site_id, channel, message_text, trigger_type, success, sent_at)
           VALUES (%s, 'telegram', 'newest-row', 'trend', 1, '2000-01-01 00:00:00+00')""",
        (site_id,),
    )
    conn.commit()
    cur.close()
    conn.close()
    assert trend_alert_due(site_id, tmp_db) is True
