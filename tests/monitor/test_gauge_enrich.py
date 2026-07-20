"""Tests for gauge search enrichment (liveness + NOAA availability)."""

from unittest.mock import patch

import pandas as pd

from monitor.gauge_enrich import annotate_liveness, annotate_noaa


def _matches():
    return [
        {"number": "03294500", "name": "OHIO RIVER AT LOUISVILLE, KY"},
        {"number": "09999999", "name": "DEAD GAUGE"},
    ]


def _iv_frame():
    # Shape after nwis.get_iv(sites=[...]) is normalized with reset_index():
    # a site_no column, a datetime column, and a value column named by param.
    return pd.DataFrame({
        "site_no": ["03294500", "03294500"],
        "datetime": pd.to_datetime(["2026-07-19T10:00:00Z",
                                    "2026-07-19T10:15:00Z"]),
        "00060": [1000.0, 1010.0],
    })


def test_annotate_liveness_flags_live_and_dead():
    with patch("monitor.gauge_enrich.nwis.get_iv",
               return_value=(_iv_frame(), {})):
        out = annotate_liveness(_matches())
    live = {m["number"]: m for m in out}
    assert live["03294500"]["live"] is True
    assert live["03294500"]["last_value"] == 1010.0
    assert live["03294500"]["last_time"] is not None
    # A site with no rows in the window is not live.
    assert live["09999999"]["live"] is False


def test_annotate_liveness_survives_api_failure():
    with patch("monitor.gauge_enrich.nwis.get_iv",
               side_effect=Exception("USGS down")):
        out = annotate_liveness(_matches())
    assert all(m["live"] is False for m in out)   # unchanged, no raise
    assert len(out) == 2


def _fake_noaa(identifier, timeout=6):
    if identifier == "03293551":
        return {"lid": "MLUK2", "station_name": "Ohio River",
                "action_stage": 21.0, "minor_flood_stage": 23.0,
                "moderate_flood_stage": 30.0, "major_flood_stage": 38.0}
    return None  # no NOAA gauge at this USGS site (HTTP 404)


def test_annotate_noaa_flags_forecast_gauges():
    matches = [
        {"number": "03293551", "name": "OHIO RIVER"},
        {"number": "09999999", "name": "NO NOAA HERE"},
    ]
    with patch("monitor.gauge_enrich.fetch_gauge_metadata",
               side_effect=_fake_noaa):
        out = annotate_noaa(matches)
    by = {m["number"]: m for m in out}
    assert by["03293551"]["noaa_has_flood"] is True
    assert by["03293551"]["noaa_lid"] == "MLUK2"
    assert by["09999999"]["noaa_has_flood"] is False
    assert by["09999999"]["noaa_lid"] is None


def test_annotate_noaa_survives_lookup_failure():
    matches = [{"number": "03293551", "name": "OHIO RIVER"}]
    with patch("monitor.gauge_enrich.fetch_gauge_metadata",
               side_effect=Exception("NWPS down")):
        out = annotate_noaa(matches)
    assert out[0]["noaa_has_flood"] is False   # no raise
    assert out[0]["noaa_lid"] is None
