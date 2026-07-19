"""Tests for USGS gauge search by station name (Monitoring Locations OGC API)."""

from unittest.mock import patch, MagicMock

import requests

from monitor.site_search import search_sites_by_name


def _feature(number, name, state="Kentucky", site_type="Stream"):
    return {
        "properties": {
            "monitoring_location_number": number,
            "monitoring_location_name": name,
            "state_name": state,
            "site_type": site_type,
        }
    }


def _fake_response(features, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {"features": features}

    def _raise():
        if status >= 400:
            raise requests.exceptions.HTTPError(f"{status} error")

    resp.raise_for_status.side_effect = _raise
    return resp


def test_single_match_returns_one_result():
    features = [_feature("03294500", "OHIO RIVER AT LOUISVILLE, KY")]
    with patch("monitor.site_search.requests.get", return_value=_fake_response(features)):
        matches, truncated, error = search_sites_by_name("ohio river at louisville")
    assert error == ""
    assert truncated is False
    assert matches == [{
        "number": "03294500",
        "name": "OHIO RIVER AT LOUISVILLE, KY",
        "state": "Kentucky",
        "site_type": "Stream",
    }]


def test_multiple_matches_are_sorted_by_name():
    features = [
        _feature("222", "MILL CREEK NEAR B"),
        _feature("111", "MILL CREEK NEAR A"),
    ]
    with patch("monitor.site_search.requests.get", return_value=_fake_response(features)):
        matches, truncated, error = search_sites_by_name("mill creek")
    assert [m["name"] for m in matches] == ["MILL CREEK NEAR A", "MILL CREEK NEAR B"]
    assert truncated is False


def test_more_than_limit_sets_truncated_and_caps_results():
    features = [_feature(str(i), f"CREEK {i}") for i in range(3)]
    with patch("monitor.site_search.requests.get", return_value=_fake_response(features)):
        matches, truncated, error = search_sites_by_name("creek", limit=2)
    assert len(matches) == 2
    assert truncated is True
    assert error == ""


def test_zero_matches_returns_empty_without_error():
    with patch("monitor.site_search.requests.get", return_value=_fake_response([])):
        matches, truncated, error = search_sites_by_name("zzzznotagauge")
    assert matches == []
    assert truncated is False
    assert error == ""


def test_http_error_returns_error_message():
    with patch("monitor.site_search.requests.get", return_value=_fake_response([], status=500)):
        matches, truncated, error = search_sites_by_name("ohio")
    assert matches == []
    assert error != ""


def test_timeout_returns_error_message():
    with patch("monitor.site_search.requests.get",
               side_effect=requests.exceptions.Timeout("slow")):
        matches, truncated, error = search_sites_by_name("ohio")
    assert matches == []
    assert "timed out" in error.lower()


def test_empty_query_returns_error_and_skips_api():
    with patch("monitor.site_search.requests.get") as mock_get:
        matches, truncated, error = search_sites_by_name("   ")
    assert matches == []
    assert error != ""
    mock_get.assert_not_called()


def test_query_is_uppercased_wildcards_stripped_and_quotes_escaped():
    with patch("monitor.site_search.requests.get", return_value=_fake_response([])) as mock_get:
        search_sites_by_name("o'brien 50%_x")
    sent_filter = mock_get.call_args.kwargs["params"]["filter"]
    # Uppercased, single quote doubled, % and _ removed, site types constrained.
    assert "O''BRIEN 50X" in sent_filter
    assert "site_type_code IN ('ST','LK')" in sent_filter


def test_non_dict_payload_does_not_raise():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = []
    resp.raise_for_status.side_effect = lambda: None
    with patch("monitor.site_search.requests.get", return_value=resp):
        matches, truncated, error = search_sites_by_name("ohio")
    assert matches == []
    assert truncated is False
    assert error != ""

    with patch("monitor.site_search.requests.get", return_value=_fake_response([None])):
        matches, truncated, error = search_sites_by_name("ohio")
    assert matches == []
    assert truncated is False
    assert error == ""


def test_all_wildcard_query_returns_error_and_skips_api():
    with patch("monitor.site_search.requests.get") as mock_get:
        matches, truncated, error = search_sites_by_name("%%__")
    assert matches == []
    assert error != ""
    mock_get.assert_not_called()
