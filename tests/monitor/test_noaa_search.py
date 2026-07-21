from unittest.mock import patch, MagicMock
import requests
from monitor.noaa_search import search_noaa_gauges_by_name, _where, _to_match


def _feat(lid, location, waterbody="Ohio River", state="KY"):
    return {"attributes": {"gaugelid": lid, "location": location,
                           "waterbody": waterbody, "state": state}}


def _resp(features, exceeded=False):
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.return_value = {"features": features, "exceededTransferLimit": exceeded}
    return r


def test_where_ands_keywords_over_location_and_waterbody():
    w = _where(["MCALPINE", "UPPER"])
    assert w == ("(UPPER(location) LIKE '%MCALPINE%' OR UPPER(waterbody) LIKE '%MCALPINE%')"
                 " AND (UPPER(location) LIKE '%UPPER%' OR UPPER(waterbody) LIKE '%UPPER%')")


def test_to_match_shape_and_skips_missing_lid():
    assert _to_match(_feat("MLUK2", "McAlpine Upper")) == {
        "lid": "MLUK2", "name": "McAlpine Upper",
        "waterbody": "Ohio River", "state": "KY"}
    assert _to_match({"attributes": {"location": "x"}}) is None
    assert _to_match("not a dict") is None       # malformed item, no raise
    assert _to_match(None) is None


def test_search_reports_capped_when_count_reaches_cap():
    feats = [_feat(f"L{i}", f"CREEK {i}") for i in range(3)]
    with patch("monitor.noaa_search.requests.get", return_value=_resp(feats)):
        _cands, capped, err = search_noaa_gauges_by_name("creek", cap=3)
    assert capped is True and err == ""


def test_search_ranks_noaa_name_match():
    feats = [_feat("MLUK2", "McAlpine Upper"), _feat("MLPK2", "McAlpine Lower")]
    with patch("monitor.noaa_search.requests.get", return_value=_resp(feats)):
        cands, capped, err = search_noaa_gauges_by_name("mcalpine upper")
    assert err == "" and capped is False
    assert cands[0]["lid"] == "MLUK2"          # exact "upper" match ranks first
    assert {c["lid"] for c in cands} == {"MLUK2", "MLPK2"}


def test_search_reports_capped():
    feats = [_feat(f"L{i}", f"CREEK {i}") for i in range(3)]
    with patch("monitor.noaa_search.requests.get", return_value=_resp(feats, exceeded=True)):
        _cands, capped, err = search_noaa_gauges_by_name("creek")
    assert capped is True and err == ""


def test_search_survives_api_failure():
    with patch("monitor.noaa_search.requests.get",
               side_effect=requests.exceptions.Timeout("slow")):
        cands, capped, err = search_noaa_gauges_by_name("ohio")
    assert cands == [] and capped is False and "timed out" in err.lower()


def test_search_empty_query_errors_without_calling_api():
    with patch("monitor.noaa_search.requests.get") as g:
        cands, capped, err = search_noaa_gauges_by_name("   ")
    assert cands == [] and err != ""
    g.assert_not_called()
