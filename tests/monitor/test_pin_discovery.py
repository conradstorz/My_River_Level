# tests/monitor/test_pin_discovery.py
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from monitor import pin_discovery
from monitor.pin_discovery import Discovery, discover, haversine_km, river_name_from_gauges

PIN = (38.28, -85.76)  # Ohio River at Louisville

POSITION = {"type": "FeatureCollection", "features": [{
    "type": "Feature",
    "geometry": {"type": "LineString",
                 "coordinates": [[-85.77, 38.279], [-85.75, 38.281]]},
    "properties": {"comid": "1234567", "name": "Ohio River"},
}]}

FAR_POSITION = {"type": "FeatureCollection", "features": [{
    "type": "Feature",
    "geometry": {"type": "LineString",
                 "coordinates": [[-85.76, 38.40], [-85.75, 38.41]]},   # ~13 km north
    "properties": {"comid": "999", "name": "Some Creek"},
}]}


def _site(number, name, lon, lat):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"identifier": f"USGS-{number}", "name": name}}


UM = {"type": "FeatureCollection", "features": [
    _site("03293551", "OHIO R US OF MCALPINE", -85.74, 38.29)]}
DM = {"type": "FeatureCollection", "features": [
    _site("03294500", "OHIO RIVER AT LOUISVILLE", -85.80, 38.27),
    _site("03294600", "OHIO R AT CANE RUN", -85.90, 38.25)]}
EMPTY = {"type": "FeatureCollection", "features": []}


def _resp(payload, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    return m


def _nldi(position=POSITION, um=UM, dm=DM):
    """Return a requests.get side effect answering the three NLDI URLs."""
    def side_effect(url, **kwargs):
        if "/comid/position" in url:
            return _resp(position)
        if "/navigation/UM/" in url:
            return _resp(um)
        if "/navigation/DM/" in url:
            return _resp(dm)
        raise AssertionError(f"unexpected URL {url}")
    return side_effect


def _catalog(*rows):
    """seriesCatalogOutput frame: rows of (site_no, parm_cd)."""
    return pd.DataFrame(rows, columns=["site_no", "parm_cd"]), None


ALL_HAVE_STAGE = _catalog(("03293551", "00065"), ("03294500", "00065"),
                          ("03294500", "00060"), ("03294600", "00060"))


def _meta(identifier, timeout=10):
    """NWPS per-gauge metadata keyed by USGS number (the listing has no usgsId)."""
    if identifier == "03293551":
        return {"lid": "MLUK2", "station_name": "McAlpine Upper", "usgs_id": "03293551",
                "action_stage": 21.0, "minor_flood_stage": 23.0,
                "moderate_flood_stage": 30.0, "major_flood_stage": 38.0}
    return None


@pytest.fixture
def nwps():
    """The live NWPS listing carries no usgsId, so usgs_id is always None here."""
    with patch("monitor.pin_discovery.gauges_near") as g, \
         patch("monitor.pin_discovery.fetch_gauge_metadata", side_effect=_meta):
        g.return_value = [
            {"lid": "MLUK2", "name": "McAlpine Upper", "usgs_id": None,
             "lat": 38.29, "lon": -85.74},
            {"lid": "XXXX1", "name": "Some Other Creek", "usgs_id": None,
             "lat": 38.30, "lon": -85.60},
        ]
        yield g


def test_haversine_known_distance():
    assert haversine_km(38.0, -85.0, 39.0, -85.0) == pytest.approx(111.2, abs=0.5)


def test_on_network_pin_proposes_stem_gauges_in_order(nwps):
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi()), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert isinstance(result, Discovery)
    assert result.snap == "on_network"
    assert result.river_name == "Ohio River"
    usgs = [c for c in result.candidates if c.kind == "usgs"]
    assert [(c.id, c.tag) for c in usgs] == [
        ("03293551", "upstream"), ("03294500", "downstream"), ("03294600", "downstream")]
    assert usgs[0].parameter_code == "00065"
    assert usgs[2].parameter_code == "00060"          # only discharge available
    assert usgs[1].distance_km < usgs[2].distance_km  # downstream sorted by distance


def test_noaa_gauge_matching_a_stem_site_inherits_its_tag(nwps):
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi()), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    noaa = {c.id: c for c in result.candidates if c.kind == "noaa"}
    assert noaa["MLUK2"].tag == "upstream" and noaa["MLUK2"].usgs_id == "03293551"
    assert noaa["XXXX1"].tag == "nearby"


def test_sites_without_stage_or_discharge_are_dropped(nwps):
    only_temp = _catalog(("03293551", "00010"), ("03294500", "00065"))
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi()), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=only_temp):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert [c.id for c in result.candidates if c.kind == "usgs"] == ["03294500"]


def test_off_network_pin_falls_back_to_bbox(nwps):
    what_sites = pd.DataFrame([
        {"site_no": "03294500", "station_nm": "OHIO RIVER AT LOUISVILLE",
         "dec_lat_va": 38.27, "dec_long_va": -85.80},
    ]), None
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi(position=FAR_POSITION)), \
         patch("monitor.pin_discovery.nwis.what_sites", return_value=what_sites), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert result.snap == "off_network"
    assert result.river_name is None
    usgs = [c for c in result.candidates if c.kind == "usgs"]
    assert [(c.id, c.tag) for c in usgs] == [("03294500", "nearest")]


def test_empty_navigation_falls_back_to_bbox(nwps):
    what_sites = pd.DataFrame([
        {"site_no": "03294500", "station_nm": "OHIO RIVER AT LOUISVILLE",
         "dec_lat_va": 38.27, "dec_long_va": -85.80},
    ]), None
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi(um=EMPTY, dm=EMPTY)), \
         patch("monitor.pin_discovery.nwis.what_sites", return_value=what_sites), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert result.snap == "on_network"
    assert result.river_name == "Ohio River"
    assert [c.tag for c in result.candidates if c.kind == "usgs"] == ["nearest"]


def test_nldi_error_falls_back_and_reports_failed_snap(nwps):
    what_sites = pd.DataFrame([
        {"site_no": "03294500", "station_nm": "OHIO RIVER AT LOUISVILLE",
         "dec_lat_va": 38.27, "dec_long_va": -85.80},
    ]), None
    with patch("monitor.pin_discovery.requests.get", side_effect=Exception("down")), \
         patch("monitor.pin_discovery.nwis.what_sites", return_value=what_sites), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert result.snap == "failed"
    assert [c.id for c in result.candidates if c.kind == "usgs"] == ["03294500"]


def test_everything_failing_yields_empty_but_valid_result():
    with patch("monitor.pin_discovery.requests.get", side_effect=Exception("down")), \
         patch("monitor.pin_discovery.nwis.what_sites", side_effect=Exception("down")), \
         patch("monitor.pin_discovery.nwis.get_info", side_effect=Exception("down")), \
         patch("monitor.pin_discovery.fetch_gauge_metadata", return_value=None), \
         patch("monitor.pin_discovery.gauges_near", return_value=[]):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert result.snap == "failed"
    assert result.candidates == []
    assert result.to_dict() == {"river_name": None, "snap": "failed", "candidates": []}


def test_to_dict_is_json_ready(nwps):
    import json
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi()), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    text = json.dumps(result.to_dict())
    assert '"kind": "usgs"' in text and '"tag": "upstream"' in text


@pytest.mark.parametrize("names,expected", [
    (["OHIO RIVER AT BIG FOUR BRIDGE AT LOUISVILLE, KY"], "Ohio River"),
    (["OHIO R US OF MCALPINE DAM @ RRB AT LOUISVILLE, KY"], "Ohio River"),
    (["OHIO RIVER AT MCALPINE DAM - HEADWATER"], "Ohio River"),
    (["S FK BEARGRASS CREEK NR LOUISVILLE, KY"], "South Fork Beargrass Creek"),
    (["Ohio River at McAlpine Upper"], "Ohio River"),
    (["OHIO RIVER AT X", "OHIO R AT Y", "SALT RIVER NR Z"], "Ohio River"),
    (["NO MARKER HERE"], None),
    ([], None),
])
def test_river_name_from_gauges(names, expected):
    assert river_name_from_gauges(names) == expected


def test_river_name_derived_from_gauges_when_nldi_has_none(nwps):
    nameless = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": POSITION["features"][0]["geometry"],
        "properties": {"comid": "1234567"},
    }]}
    with patch("monitor.pin_discovery.requests.get", side_effect=_nldi(position=nameless)), \
         patch("monitor.pin_discovery.nwis.get_info", return_value=ALL_HAVE_STAGE):
        result = discover(*PIN, reach_km=50, fallback_radius_miles=25)
    assert result.snap == "on_network"
    assert result.river_name == "Ohio River"
