"""Tests for USGS ranked keyword search (Monitoring Locations OGC API)."""

from unittest.mock import patch, MagicMock

import requests

from monitor.site_search import (
    search_sites_by_name,
    _tokenize,
    _match_group,
    _score,
    _fetch,
)


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


# ── pure helpers ──────────────────────────────────────────────────────────

def test_tokenize_splits_and_uppercases_dropping_short_tokens():
    assert _tokenize("Ohio River at Louisville, KY") == [
        "OHIO", "RIVER", "AT", "LOUISVILLE", "KY",
    ]
    assert _tokenize("a b cd") == ["CD"]


def test_match_group_expands_full_state_name():
    assert _match_group("KENTUCKY") == (
        "(monitoring_location_name LIKE '%KENTUCKY%' "
        "OR monitoring_location_name LIKE '%, KY%')"
    )


def test_match_group_plain_token_and_escaping():
    assert _match_group("LOUISVILLE") == "monitoring_location_name LIKE '%LOUISVILLE%'"
    assert "O''BRIEN" in _match_group("O'BRIEN")


def test_score_exact_tokens_sum_to_one_each():
    assert _score("OHIO RIVER AT LOUISVILLE, KY", ["OHIO", "RIVER"]) == 2.0


def test_score_ranks_typo_nearest_word_highest():
    tokens = ["LOUSVILLE", "OHIO", "RIVER"]
    louisville = _score("OHIO RIVER AT LOUISVILLE, KY", tokens)
    cincinnati = _score("OHIO RIVER AT CINCINNATI, OH", tokens)
    assert louisville > cincinnati


def test_score_credits_state_name_via_abbreviation():
    # "KENTUCKY" isn't in the name, but ", KY" is → full credit.
    assert _score("OHIO RIVER AT LOUISVILLE, KY", ["KENTUCKY"]) == 1.0


# ── _fetch helper ─────────────────────────────────────────────────────────

def test_fetch_success_returns_features():
    feats = [_feature("03294500", "OHIO RIVER AT LOUISVILLE, KY")]
    with patch("monitor.site_search.requests.get", return_value=_fake_response(feats)):
        features, error = _fetch("monitoring_location_name LIKE '%OHIO%'", 5)
    assert error == ""
    assert features == feats


def test_fetch_appends_site_type_filter():
    with patch("monitor.site_search.requests.get", return_value=_fake_response([])) as mock_get:
        _fetch("monitoring_location_name LIKE '%OHIO%'", 5)
    sent = mock_get.call_args.kwargs["params"]["filter"]
    assert sent.endswith("AND site_type_code IN ('ST','LK')")
    assert "filter-lang" not in mock_get.call_args.kwargs["params"]


def test_fetch_timeout_returns_error():
    with patch("monitor.site_search.requests.get",
               side_effect=requests.exceptions.Timeout("slow")):
        features, error = _fetch("x", 5)
    assert features == []
    assert "timed out" in error.lower()


def test_fetch_http_error_returns_error():
    with patch("monitor.site_search.requests.get", return_value=_fake_response([], status=500)):
        features, error = _fetch("x", 5)
    assert features == []
    assert error != ""


def test_fetch_non_dict_payload_does_not_raise():
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = ["not", "a", "dict"]
    with patch("monitor.site_search.requests.get", return_value=resp):
        features, error = _fetch("x", 5)
    assert features == []
    assert error != ""


# ── orchestration ─────────────────────────────────────────────────────────

def test_empty_query_returns_error_and_skips_api():
    with patch("monitor.site_search._fetch") as mock_fetch:
        matches, truncated, error = search_sites_by_name("   ")
    assert matches == []
    assert error != ""
    mock_fetch.assert_not_called()


def test_clean_query_ranks_shortest_exact_name_first():
    long = _feature("03293500", "OHIO RIVER AT WATER TOWER AT LOUISVILLE, KY")
    short = _feature("03294500", "OHIO RIVER AT LOUISVILLE, KY")
    # Step 1 returns everything (unsorted); both contain all keywords.
    with patch("monitor.site_search._fetch", return_value=([long, short], "")):
        matches, truncated, error = search_sites_by_name("ohio river louisville")
    assert error == ""
    assert [m["number"] for m in matches] == ["03294500", "03293500"]


def test_typo_query_relaxes_and_ranks_fuzzy_match_first():
    anchor_hit = _feature("x", "OHIO RIVER")  # non-empty existence result
    cincinnati = _feature("111", "OHIO RIVER AT CINCINNATI, OH")
    louisville = _feature("03294500", "OHIO RIVER AT LOUISVILLE, KY")
    # step1 (all 3 keywords) empty; existence: LOUSVILLE none, OHIO yes, RIVER yes;
    # anchor fetch returns the Ohio-River pool.
    side_effect = [
        ([], ""),               # step 1: AND(all) -> empty (typo present)
        ([], ""),               # existence: LOUSVILLE -> none
        ([anchor_hit], ""),     # existence: OHIO group -> match
        ([anchor_hit], ""),     # existence: RIVER -> match
        ([cincinnati, louisville], ""),  # anchor pool
    ]
    with patch("monitor.site_search._fetch", side_effect=side_effect):
        matches, truncated, error = search_sites_by_name("lousville ohio river")
    assert error == ""
    assert matches[0]["number"] == "03294500"


def test_disjoint_keywords_rank_rarest_keyword_first():
    # "mcalpine upper": both keywords exist in USGS data but never co-occur in
    # one name (USGS names the site "OHIO R US OF MCALPINE DAM", not "McAlpine
    # Upper"). The strict AND yields nothing; the search ORs the surviving
    # keywords AND ranks matches of the rarer keyword (MCALPINE, 2 hits) ahead
    # of the common one (UPPER, 3 hits), so "upper" can't bury the McAlpine gauge.
    mcalpine_ohio = _feature("03293551",
                             "OHIO R US OF MCALPINE DAM @ RRB AT LOUISVILLE, KY")
    mcalpine_creek = _feature("02146600",
                              "MCALPINE CR AT SARDIS ROAD NEAR CHARLOTTE, NC")
    upper1 = _feature("111", "UPPER IOWA RIVER AT DECORAH, IA")
    upper2 = _feature("222", "UPPER TRUCKEE RIVER, CA")
    upper3 = _feature("333", "UPPER MISSISSIPPI RIVER, MN")
    side_effect = [
        ([], ""),                                   # step 1: AND(all) -> empty
        ([mcalpine_creek], ""),                     # existence: MCALPINE -> match
        ([upper1], ""),                             # existence: UPPER -> match
        ([], ""),                                   # AND(anchors) -> still empty
        ([mcalpine_ohio, mcalpine_creek], ""),      # OR pool: MCALPINE (rarer)
        ([upper1, upper2, upper3], ""),             # OR pool: UPPER (commoner)
    ]
    with patch("monitor.site_search._fetch", side_effect=side_effect):
        matches, truncated, error = search_sites_by_name("mcalpine upper", limit=25)
    assert error == ""
    numbers = [m["number"] for m in matches]
    assert "03293551" in numbers  # the McAlpine gauge surfaces (was 0 results)
    # Both McAlpine (rarer-keyword) sites rank ahead of every UPPER-only site.
    assert numbers.index("03293551") < numbers.index("111")
    assert numbers.index("02146600") < numbers.index("111")


def test_no_matches_returns_empty():
    side_effect = [
        ([], ""),  # step 1
        ([], ""),  # existence token 1
        ([], ""),  # existence token 2
    ]
    with patch("monitor.site_search._fetch", side_effect=side_effect):
        matches, truncated, error = search_sites_by_name("zzzz qqqq")
    assert matches == []
    assert truncated is False
    assert error == ""


def test_api_error_propagates():
    with patch("monitor.site_search._fetch",
               return_value=([], "USGS search failed. Please try again later.")):
        matches, truncated, error = search_sites_by_name("ohio")
    assert matches == []
    assert "failed" in error.lower()


def test_truncated_when_more_candidates_than_limit():
    feats = [_feature(str(i), f"MILL CREEK {i:02d}") for i in range(30)]
    with patch("monitor.site_search._fetch", return_value=(feats, "")):
        matches, truncated, error = search_sites_by_name("mill creek", limit=25)
    assert len(matches) == 25
    assert truncated is True
