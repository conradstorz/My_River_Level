from unittest.mock import patch
from monitor.gauge_discovery import combined_site_matches


def _usgs(*a, **k):
    return ([{"number": "03293551", "name": "OHIO R US OF MCALPINE DAM",
              "state": "Kentucky", "site_type": "Stream"}], False, "")


def _noaa(*a, **k):
    return ([{"lid": "MLUK2", "name": "McAlpine Upper",
              "waterbody": "Ohio River", "state": "KY"}], False, "")


def _meta(lid, timeout=10):
    return {"lid": "MLUK2", "station_name": "Ohio River at McAlpine Upper",
            "usgs_id": "03293551", "action_stage": 21.0,
            "minor_flood_stage": None, "moderate_flood_stage": None,
            "major_flood_stage": None}


def test_noaa_hit_dedups_onto_existing_usgs_row_with_tag():
    with patch("monitor.gauge_discovery.search_sites_by_name", side_effect=_usgs), \
         patch("monitor.gauge_discovery.search_noaa_gauges_by_name", side_effect=_noaa), \
         patch("monitor.gauge_discovery.fetch_gauge_metadata", side_effect=_meta):
        rows, noaa_only, capped, err = combined_site_matches("mcalpine upper")
    assert err == "" and noaa_only == 0
    assert len(rows) == 1                       # deduped by site number
    assert rows[0]["number"] == "03293551"
    assert rows[0]["noaa_name"] == "McAlpine Upper"   # tagged


def test_noaa_only_hit_with_usgsid_becomes_addable_row():
    def _usgs_empty(*a, **k):
        return ([], False, "")
    with patch("monitor.gauge_discovery.search_sites_by_name", side_effect=_usgs_empty), \
         patch("monitor.gauge_discovery.search_noaa_gauges_by_name", side_effect=_noaa), \
         patch("monitor.gauge_discovery.fetch_gauge_metadata", side_effect=_meta):
        rows, noaa_only, capped, err = combined_site_matches("mcalpine upper")
    assert [r["number"] for r in rows] == ["03293551"]
    assert rows[0]["noaa_name"] == "McAlpine Upper"


def test_noaa_hit_without_usgsid_is_counted_not_added():
    def _meta_no_usgs(lid, timeout=10):
        m = _meta(lid); m["usgs_id"] = None
        return m
    def _usgs_empty(*a, **k):
        return ([], False, "")
    with patch("monitor.gauge_discovery.search_sites_by_name", side_effect=_usgs_empty), \
         patch("monitor.gauge_discovery.search_noaa_gauges_by_name", side_effect=_noaa), \
         patch("monitor.gauge_discovery.fetch_gauge_metadata", side_effect=_meta_no_usgs):
        rows, noaa_only, capped, err = combined_site_matches("x")
    assert rows == [] and noaa_only == 1


def test_noaa_failure_still_returns_usgs_rows():
    def _noaa_fail(*a, **k):
        return ([], False, "NOAA search failed. Please try again later.")
    with patch("monitor.gauge_discovery.search_sites_by_name", side_effect=_usgs), \
         patch("monitor.gauge_discovery.search_noaa_gauges_by_name", side_effect=_noaa_fail):
        rows, noaa_only, capped, err = combined_site_matches("mcalpine")
    assert err == "" and [r["number"] for r in rows] == ["03293551"]


def test_both_sources_fail_surfaces_error():
    def _usgs_fail(*a, **k):
        return ([], False, "USGS search failed. Please try again later.")
    def _noaa_fail(*a, **k):
        return ([], False, "NOAA search failed. Please try again later.")
    with patch("monitor.gauge_discovery.search_sites_by_name", side_effect=_usgs_fail), \
         patch("monitor.gauge_discovery.search_noaa_gauges_by_name", side_effect=_noaa_fail):
        rows, noaa_only, capped, err = combined_site_matches("x")
    assert rows == [] and err != ""
