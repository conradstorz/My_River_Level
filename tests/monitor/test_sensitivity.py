import pytest
from monitor.scheduler import alert_allowed


@pytest.mark.parametrize("sensitivity,alert_type,severity,expected", [
    # floods: NOAA category changes and SEVERE HIGH only
    ("floods", "noaa_transition", None, True),
    ("floods", "transition", "SEVERE HIGH", True),
    ("floods", "reminder", "SEVERE HIGH", True),
    ("floods", "transition", "HIGH", False),
    ("floods", "transition", "LOW", False),
    ("floods", "transition", "SEVERE LOW", False),
    ("floods", "transition", "NORMAL", False),
    ("floods", "trend", None, False),
    # unusual: adds LOW / HIGH / SEVERE LOW and the return to NORMAL
    ("unusual", "transition", "HIGH", True),
    ("unusual", "transition", "LOW", True),
    ("unusual", "transition", "SEVERE LOW", True),
    ("unusual", "reminder", "LOW", True),
    ("unusual", "transition", "NORMAL", True),
    ("unusual", "trend", None, False),
    ("unusual", "noaa_transition", None, True),
    # all: everything
    ("all", "trend", None, True),
    ("all", "transition", "NORMAL", True),
    ("all", "noaa_transition", None, True),
    # unknown dial behaves like the default 'unusual'
    ("weird", "trend", None, False),
    ("weird", "transition", "HIGH", True),
])
def test_alert_allowed_truth_table(sensitivity, alert_type, severity, expected):
    assert alert_allowed(sensitivity, alert_type, severity) is expected
