from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from monitor.noaa_client import (classify_noaa_condition, fetch_gauge_metadata,
                                 fetch_current_stage, fetch_forecast)


# ── Condition classifier ────────────────────────────────────────────────────

def test_classify_normal():
    assert classify_noaa_condition(15.0, 21.0, 23.0, 30.0, 38.0) == "Normal"

def test_classify_action():
    assert classify_noaa_condition(21.5, 21.0, 23.0, 30.0, 38.0) == "Action"

def test_classify_minor():
    assert classify_noaa_condition(24.0, 21.0, 23.0, 30.0, 38.0) == "Minor"

def test_classify_moderate():
    assert classify_noaa_condition(31.0, 21.0, 23.0, 30.0, 38.0) == "Moderate"

def test_classify_major():
    assert classify_noaa_condition(40.0, 21.0, 23.0, 30.0, 38.0) == "Major"

def test_classify_none_stage():
    assert classify_noaa_condition(None, 21.0, 23.0, 30.0, 38.0) == "Unknown"

def test_classify_missing_thresholds():
    # Missing upper thresholds — only action is set, so 50ft is still "Action"
    assert classify_noaa_condition(50.0, 21.0, None, None, None) == "Action"


# ── API fetches ─────────────────────────────────────────────────────────────

def _mock_metadata_response():
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "lid": "MLUK2",
        "name": "Ohio River at McAlpine Upper",
        "flood": {
            "categories": [
                {"name": "action",   "stage": 21.0},
                {"name": "minor",    "stage": 23.0},
                {"name": "moderate", "stage": 30.0},
                {"name": "major",    "stage": 38.0},
            ]
        }
    }
    return mock


def test_fetch_gauge_metadata():
    with patch("monitor.noaa_client.requests.get", return_value=_mock_metadata_response()):
        meta = fetch_gauge_metadata("MLUK2")
    assert meta["station_name"] == "Ohio River at McAlpine Upper"
    assert meta["action_stage"] == 21.0
    assert meta["minor_flood_stage"] == 23.0
    assert meta["moderate_flood_stage"] == 30.0
    assert meta["major_flood_stage"] == 38.0


def test_fetch_gauge_metadata_http_error():
    mock = MagicMock()
    mock.status_code = 404
    with patch("monitor.noaa_client.requests.get", return_value=mock):
        meta = fetch_gauge_metadata("BADLID")
    assert meta is None


def _mock_metadata_dict_response():
    """Real NWPS shape: flood.categories is a dict keyed by name, plus a lid."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "lid": "MLUK2",
        "usgsId": "03293551",
        "name": "Ohio River at McAlpine Upper",
        "flood": {
            "categories": {
                "action":   {"stage": 21.0, "flow": 484486},
                "minor":    {"stage": 23.0, "flow": 512700},
                "moderate": {"stage": 30.0, "flow": 630001},
                "major":    {"stage": 38.0, "flow": 783550},
            }
        },
    }
    return mock


def test_fetch_gauge_metadata_dict_categories_and_lid():
    with patch("monitor.noaa_client.requests.get",
               return_value=_mock_metadata_dict_response()):
        meta = fetch_gauge_metadata("03293551")  # looked up by USGS number
    assert meta["lid"] == "MLUK2"
    assert meta["station_name"] == "Ohio River at McAlpine Upper"
    assert meta["action_stage"] == 21.0
    assert meta["minor_flood_stage"] == 23.0
    assert meta["moderate_flood_stage"] == 30.0
    assert meta["major_flood_stage"] == 38.0


def test_fetch_gauge_metadata_none_identifier_returns_none():
    # A None/non-string identifier must degrade to None, never raise.
    assert fetch_gauge_metadata(None) is None


def test_fetch_current_stage():
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "data": [
            {"validTime": "2026-03-07T12:00:00Z", "primary": 17.16},
            {"validTime": "2026-03-07T12:05:00Z", "primary": 17.20},
        ]
    }
    with patch("monitor.noaa_client.requests.get", return_value=mock):
        stage = fetch_current_stage("MLUK2")
    assert stage == 17.20


def test_fetch_current_stage_empty_data():
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"data": []}
    with patch("monitor.noaa_client.requests.get", return_value=mock):
        stage = fetch_current_stage("MLUK2")
    assert stage is None


def test_fetch_gauge_metadata_returns_usgs_id():
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "lid": "MLUK2", "name": "Ohio River at McAlpine Upper",
        "usgsId": "03293551",
        "flood": {"categories": {"action": {"stage": 21.0}}},
    }
    with patch("monitor.noaa_client.requests.get", return_value=mock):
        meta = fetch_gauge_metadata("MLUK2")
    assert meta["usgs_id"] == "03293551"
    assert meta["lid"] == "MLUK2"


# ── Forecast fetch ──────────────────────────────────────────────────────────

def _mock_json(payload, status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = payload
    return mock


def test_fetch_forecast_flat_data_shape():
    payload = {
        "issuedTime": "2026-08-12T12:00:00Z",
        "primaryUnits": "ft",
        "data": [
            {"validTime": "2026-08-13T12:00:00Z", "primary": 21.5},
            {"validTime": "2026-08-14T12:00:00Z", "primary": 23.0},
        ],
    }
    with patch("monitor.noaa_client.requests.get",
               return_value=_mock_json(payload)) as mock_get:
        forecast = fetch_forecast("MLUK2")

    assert mock_get.call_args[0][0].endswith("/gauges/mluk2/stageflow/forecast")
    assert forecast["issued_at"] == datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
    assert [p["stage"] for p in forecast["points"]] == [21.5, 23.0]
    assert forecast["points"][0]["valid_at"] == datetime(2026, 8, 13, 12,
                                                         tzinfo=timezone.utc)


def test_fetch_forecast_nested_dict_shape():
    """NWPS also nests the series under a named section, like the metadata call."""
    payload = {
        "forecast": {
            "issuedTime": "2026-08-12T06:00:00+00:00",
            "data": [{"validTime": "2026-08-13T06:00:00Z", "primary": 19.25}],
        }
    }
    with patch("monitor.noaa_client.requests.get",
               return_value=_mock_json(payload)):
        forecast = fetch_forecast("MLUK2")

    assert forecast["issued_at"] == datetime(2026, 8, 12, 6, tzinfo=timezone.utc)
    assert forecast["points"] == [
        {"valid_at": datetime(2026, 8, 13, 6, tzinfo=timezone.utc),
         "stage": 19.25}
    ]


def test_fetch_forecast_bare_list_payload():
    payload = [{"validTime": "2026-08-13T06:00:00Z", "stage": 12.5}]
    with patch("monitor.noaa_client.requests.get",
               return_value=_mock_json(payload)):
        forecast = fetch_forecast("MLUK2")
    assert forecast["points"][0]["stage"] == 12.5
    # No issue time in the payload — fall back to a timezone-aware "now".
    assert forecast["issued_at"].tzinfo is not None


def test_fetch_forecast_timestamps_are_timezone_aware():
    payload = {"issuedTime": "2026-08-12T12:00:00Z",
               "data": [{"validTime": "2026-08-13T12:00:00", "primary": 21.5}]}
    with patch("monitor.noaa_client.requests.get",
               return_value=_mock_json(payload)):
        forecast = fetch_forecast("MLUK2")
    assert forecast["issued_at"].tzinfo is not None
    assert forecast["points"][0]["valid_at"].tzinfo is not None


def test_fetch_forecast_skips_unusable_points():
    payload = {
        "issuedTime": "2026-08-12T12:00:00Z",
        "data": [
            {"validTime": "not-a-time", "primary": 21.5},
            {"validTime": "2026-08-13T12:00:00Z", "primary": None},
            {"validTime": "2026-08-14T12:00:00Z", "primary": 23.0},
        ],
    }
    with patch("monitor.noaa_client.requests.get",
               return_value=_mock_json(payload)):
        forecast = fetch_forecast("MLUK2")
    assert [p["stage"] for p in forecast["points"]] == [23.0]


def test_fetch_forecast_without_points_returns_none():
    with patch("monitor.noaa_client.requests.get",
               return_value=_mock_json({"data": []})):
        assert fetch_forecast("MLUK2") is None


def test_fetch_forecast_http_error_returns_none():
    with patch("monitor.noaa_client.requests.get",
               return_value=_mock_json({}, status_code=404)):
        assert fetch_forecast("BADLID") is None


def test_fetch_forecast_network_error_returns_none():
    with patch("monitor.noaa_client.requests.get",
               side_effect=RuntimeError("connection reset")):
        assert fetch_forecast("MLUK2") is None


def test_fetch_forecast_none_lid_returns_none():
    assert fetch_forecast(None) is None


# ── fetch_forecast_result: distinguishing "no forecast" from "couldn't check" ──

def test_fetch_forecast_result_reports_none_on_http_404(mocker):
    """A 404 means NOAA genuinely publishes no forecast for this gauge."""
    from monitor.noaa_client import fetch_forecast_result
    resp = mocker.Mock(status_code=404)
    mocker.patch("monitor.noaa_client.requests.get", return_value=resp)
    result = fetch_forecast_result("abcd1")
    assert result["status"] == "none"
    assert result["forecast"] is None


def test_fetch_forecast_result_reports_none_on_empty_series(mocker):
    """A 200 with an empty series also means no forecast is published."""
    from monitor.noaa_client import fetch_forecast_result
    resp = mocker.Mock(status_code=200)
    resp.json.return_value = {"data": []}
    mocker.patch("monitor.noaa_client.requests.get", return_value=resp)
    assert fetch_forecast_result("abcd1")["status"] == "none"


def test_fetch_forecast_result_reports_error_on_network_failure(mocker):
    """A network failure is 'unknown', never 'no forecast published'."""
    from monitor.noaa_client import fetch_forecast_result
    mocker.patch("monitor.noaa_client.requests.get",
                 side_effect=OSError("connection reset"))
    result = fetch_forecast_result("abcd1")
    assert result["status"] == "error"
    assert result["forecast"] is None


def test_fetch_forecast_result_reports_error_on_http_503(mocker):
    """A 503 is NOAA being down, not a statement about the gauge."""
    from monitor.noaa_client import fetch_forecast_result
    resp = mocker.Mock(status_code=503)
    mocker.patch("monitor.noaa_client.requests.get", return_value=resp)
    assert fetch_forecast_result("abcd1")["status"] == "error"


def test_fetch_forecast_result_returns_ok_with_points(mocker):
    from monitor.noaa_client import fetch_forecast_result
    resp = mocker.Mock(status_code=200)
    resp.json.return_value = {
        "data": [{"validTime": "2026-08-13T12:00:00Z", "primary": 15.5}]
    }
    mocker.patch("monitor.noaa_client.requests.get", return_value=resp)
    result = fetch_forecast_result("abcd1")
    assert result["status"] == "ok"
    assert len(result["forecast"]["points"]) == 1
