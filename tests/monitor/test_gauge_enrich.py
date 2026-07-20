"""Tests for gauge search enrichment (liveness + NOAA availability)."""

from unittest.mock import patch

import pandas as pd

from monitor.gauge_enrich import annotate_liveness


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
