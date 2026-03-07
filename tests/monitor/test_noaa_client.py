from unittest.mock import patch, MagicMock
from monitor.noaa_client import classify_noaa_condition, fetch_gauge_metadata, fetch_current_stage


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
