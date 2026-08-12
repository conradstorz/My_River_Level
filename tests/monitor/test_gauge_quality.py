"""Tests for the per-gauge flood-prediction grading rubric."""

from datetime import datetime, timedelta, timezone

import pytest

from db.models import (get_all_noaa_gauges, get_gauge_quality,
                       get_or_create_noaa_gauge, record_forecast_points,
                       record_noaa_observation)
from monitor.gauge_quality import (MIN_SAMPLES, forecast_error_samples,
                                   score_all_gauges, score_gauge)


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _gauge(tmp_db, lid="abcd1", thresholds=(10.0, 12.0, 14.0, 16.0)):
    """Create a NOAA gauge row and return it the way score_gauge expects it."""
    get_or_create_noaa_gauge(lid, "Ohio River at Testville", *thresholds, tmp_db)
    return [g for g in get_all_noaa_gauges(tmp_db) if g["lid"] == lid][0]


def _seed_pairs(tmp_db, lid, count, error_ft, horizon_hours=24,
                newest_valid_offset_hours=0):
    """Archive `count` forecast points plus the observations they predicted.

    Pairs are spaced three hours apart so forecast and observation rows stay
    unique. `error_ft` is the absolute miss in feet for every pair.
    """
    now = _now()
    for i in range(count):
        valid = now - timedelta(hours=newest_valid_offset_hours + i * 3)
        issued = valid - timedelta(hours=horizon_hours)
        record_forecast_points(lid, issued,
                               [{"valid_at": valid, "stage": 10.0 + error_ft}],
                               tmp_db)
        record_noaa_observation(lid, 10.0, valid, tmp_db)


# -- Rubric branch 1: no flood categories -----------------------------------

def test_gauge_without_flood_categories_grades_f(tmp_db):
    gauge = _gauge(tmp_db, thresholds=(None, None, None, None))
    result = score_gauge(gauge, tmp_db)
    assert result["grade"] == "F"
    assert result["headline"] == "Not usable for flood warning"
    assert result["detail"] == ("NOAA publishes no flood-stage thresholds for "
                                "this gauge, so it cannot tell you when "
                                "flooding starts.")
    assert result["has_flood_categories"] is False


# -- Rubric branch 2: no forecast published ---------------------------------

def test_gauge_without_forecast_grades_d(tmp_db):
    gauge = _gauge(tmp_db)
    record_noaa_observation("abcd1", 11.0, _now() - timedelta(hours=1), tmp_db)
    result = score_gauge(gauge, tmp_db)
    assert result["grade"] == "D"
    assert result["headline"] == "Observation only"
    assert result["detail"] == ("This gauge reports current levels but NOAA "
                                "publishes no forecast for it, so you get no "
                                "advance warning.")
    assert result["publishes_forecast"] is False


# -- Rubric branch 3: not reporting -----------------------------------------

def test_gauge_with_stale_observations_grades_f(tmp_db):
    gauge = _gauge(tmp_db)
    _seed_pairs(tmp_db, "abcd1", 12, 0.3, newest_valid_offset_hours=96)
    result = score_gauge(gauge, tmp_db)
    assert result["grade"] == "F"
    assert result["headline"] == "Not reporting"
    assert result["detail"] == "No readings in the last 24 hours."
    assert result["reporting"] is False


# -- Rubric branch 4: too few samples ---------------------------------------

def test_gauge_with_too_few_samples_is_unrated(tmp_db):
    gauge = _gauge(tmp_db)
    _seed_pairs(tmp_db, "abcd1", 3, 0.3)
    result = score_gauge(gauge, tmp_db)
    assert result["grade"] == "Unrated"
    assert result["headline"] == "Collecting accuracy data"
    assert result["samples"] == 3
    assert str(MIN_SAMPLES) in result["detail"]
    assert "3 so far" in result["detail"]


def test_brand_new_gauge_is_unrated_not_a_letter(tmp_db):
    """Cold start: thresholds and a forecast exist but nothing is scored yet."""
    gauge = _gauge(tmp_db)
    now = _now()
    record_forecast_points("abcd1", now,
                           [{"valid_at": now + timedelta(hours=24),
                             "stage": 11.0}], tmp_db)
    record_noaa_observation("abcd1", 11.0, now - timedelta(hours=1), tmp_db)
    result = score_gauge(gauge, tmp_db)
    assert result["grade"] == "Unrated"
    assert result["samples"] == 0
    assert result["mae_24h"] is None


# -- Rubric branch 5: graded on 24 h mean absolute error --------------------

def test_accurate_gauge_grades_a(tmp_db):
    gauge = _gauge(tmp_db)
    _seed_pairs(tmp_db, "abcd1", 12, 0.3)
    result = score_gauge(gauge, tmp_db)
    assert result["grade"] == "A"
    assert result["headline"] == "Reliable flood predictions"
    assert result["samples"] == 12
    assert result["mae_24h"] == pytest.approx(0.3)
    assert "0.30 ft" in result["detail"]
    assert "12 forecasts checked" in result["detail"]


def test_middling_gauge_grades_b(tmp_db):
    gauge = _gauge(tmp_db)
    _seed_pairs(tmp_db, "abcd1", 12, 0.75)
    result = score_gauge(gauge, tmp_db)
    assert result["grade"] == "B"
    assert result["headline"] == "Good"


def test_loose_gauge_grades_c(tmp_db):
    gauge = _gauge(tmp_db)
    _seed_pairs(tmp_db, "abcd1", 12, 1.5)
    result = score_gauge(gauge, tmp_db)
    assert result["grade"] == "C"
    assert result["headline"] == "Fair"
    assert result["mae_24h"] == pytest.approx(1.5)


def test_wild_gauge_grades_d(tmp_db):
    gauge = _gauge(tmp_db)
    _seed_pairs(tmp_db, "abcd1", 12, 2.5)
    result = score_gauge(gauge, tmp_db)
    assert result["grade"] == "D"
    assert result["headline"] == "Unreliable"


def test_detail_appends_48_hour_figure_when_available(tmp_db):
    gauge = _gauge(tmp_db)
    _seed_pairs(tmp_db, "abcd1", 12, 0.3)
    # A second batch of 48-hour-ahead forecasts for the same observations.
    for i in range(12):
        valid = _now() - timedelta(hours=i * 3)
        record_forecast_points("abcd1", valid - timedelta(hours=48),
                               [{"valid_at": valid, "stage": 10.9}], tmp_db)
    result = score_gauge(gauge, tmp_db)
    assert result["mae_48h"] == pytest.approx(0.9)
    assert "48-hour" in result["detail"]


# -- forecast_error_samples -------------------------------------------------

def test_forecast_error_samples_skips_points_without_a_nearby_observation(tmp_db):
    _gauge(tmp_db)
    now = _now()
    valid = now - timedelta(hours=3)
    record_forecast_points("abcd1", valid - timedelta(hours=24),
                           [{"valid_at": valid, "stage": 12.0}], tmp_db)
    # The only observation is a full day from valid_at, so it must not match.
    record_noaa_observation("abcd1", 10.0, valid - timedelta(hours=24), tmp_db)
    assert forecast_error_samples("abcd1", 24, db_path=tmp_db) == []


def test_forecast_error_samples_ignores_other_horizons(tmp_db):
    _gauge(tmp_db)
    _seed_pairs(tmp_db, "abcd1", 4, 0.5, horizon_hours=48)
    assert forecast_error_samples("abcd1", 24, db_path=tmp_db) == []
    assert len(forecast_error_samples("abcd1", 48, db_path=tmp_db)) == 4


def test_forecast_error_samples_uses_the_closest_observation(tmp_db):
    _gauge(tmp_db)
    now = _now()
    valid = now - timedelta(hours=3)
    record_forecast_points("abcd1", valid - timedelta(hours=24),
                           [{"valid_at": valid, "stage": 12.0}], tmp_db)
    record_noaa_observation("abcd1", 11.5, valid - timedelta(minutes=10), tmp_db)
    record_noaa_observation("abcd1", 8.0, valid - timedelta(minutes=80), tmp_db)
    samples = forecast_error_samples("abcd1", 24, db_path=tmp_db)
    assert samples == pytest.approx([0.5])


# -- score_all_gauges -------------------------------------------------------

def test_score_all_gauges_persists_grade_and_wording(tmp_db):
    _gauge(tmp_db)
    _seed_pairs(tmp_db, "abcd1", 12, 0.3)
    assert score_all_gauges(tmp_db) == 1
    stored = get_gauge_quality("abcd1", tmp_db)
    assert stored["grade"] == "A"
    assert "Reliable flood predictions" in stored["detail"]
    assert "0.30 ft" in stored["detail"]


def test_score_all_gauges_persists_observation_only_wording(tmp_db):
    _gauge(tmp_db)
    assert score_all_gauges(tmp_db) == 1
    stored = get_gauge_quality("abcd1", tmp_db)
    assert stored["grade"] == "D"  # no forecast archived yet
    assert "Observation only" in stored["detail"]
