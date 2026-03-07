"""
Tests for USGS site validation.

Root cause captured: site 10164002 was registered with an invalid USGS site
number. No validation was performed at registration time, so the polling
thread logged "No interval data" every 15 minutes indefinitely.

These tests ensure that invalid site numbers are rejected at registration,
and that valid sites return the authoritative station name from USGS.
"""

import pytest
from unittest.mock import patch, MagicMock
import pandas as pd

from monitor.site_validation import validate_usgs_site


# ── validate_usgs_site ────────────────────────────────────────────────────────

def _mock_get_info_success(station_name="OHIO RIVER AT LOUISVILLE, KY"):
    """Return a mock get_info result with a real station name."""
    df = pd.DataFrame({"station_nm": [station_name], "site_no": ["03294500"]})
    return df, {}


def _mock_get_info_not_found(*args, **kwargs):
    raise Exception("Page Not Found Error. May be the result of an empty query.")


def test_invalid_site_number_returns_not_valid():
    """A site number that USGS 404s should be reported as invalid."""
    with patch("monitor.site_validation.nwis.get_info", side_effect=_mock_get_info_not_found):
        is_valid, station_name, error = validate_usgs_site("10164002")
    assert is_valid is False
    assert station_name == ""
    assert "not found" in error.lower()


def test_valid_site_number_returns_valid():
    with patch("monitor.site_validation.nwis.get_info", return_value=_mock_get_info_success()):
        is_valid, station_name, error = validate_usgs_site("03294500")
    assert is_valid is True
    assert error == ""


def test_valid_site_returns_station_name_from_usgs():
    """The station name must come from USGS, not be left to user input."""
    with patch("monitor.site_validation.nwis.get_info",
               return_value=_mock_get_info_success("OHIO RIVER AT LOUISVILLE, KY")):
        is_valid, station_name, _ = validate_usgs_site("03294500")
    assert station_name == "OHIO RIVER AT LOUISVILLE, KY"


def test_empty_dataframe_treated_as_not_found():
    """get_info can return an empty DataFrame for some queries."""
    df = pd.DataFrame()
    with patch("monitor.site_validation.nwis.get_info", return_value=(df, {})):
        is_valid, station_name, error = validate_usgs_site("00000000")
    assert is_valid is False
    assert "not found" in error.lower()


def test_network_error_returns_not_valid():
    """Unexpected network errors should also fail gracefully."""
    with patch("monitor.site_validation.nwis.get_info",
               side_effect=ConnectionError("Network unreachable")):
        is_valid, station_name, error = validate_usgs_site("03294500")
    assert is_valid is False
    assert error != ""
