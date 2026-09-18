import pytest
from monitor.scheduler import alert_allowed


@pytest.mark.parametrize("sensitivity,alert_type,severity,previous_severity,expected", [
    # floods: NOAA category changes and SEVERE HIGH only
    ("floods", "noaa_transition", None, None, True),
    ("floods", "transition", "SEVERE HIGH", None, True),
    ("floods", "reminder", "SEVERE HIGH", None, True),
    ("floods", "transition", "HIGH", None, False),
    ("floods", "transition", "LOW", None, False),
    ("floods", "transition", "SEVERE LOW", None, False),
    ("floods", "transition", "NORMAL", None, False),
    ("floods", "trend", None, None, False),
    # unusual: adds LOW / HIGH / SEVERE LOW and the return to NORMAL
    ("unusual", "transition", "HIGH", None, True),
    ("unusual", "transition", "LOW", None, True),
    ("unusual", "transition", "SEVERE LOW", None, True),
    ("unusual", "reminder", "LOW", None, True),
    ("unusual", "transition", "NORMAL", None, True),
    ("unusual", "trend", None, None, False),
    ("unusual", "noaa_transition", None, None, True),
    # all: everything
    ("all", "trend", None, None, True),
    ("all", "transition", "NORMAL", None, True),
    ("all", "noaa_transition", None, None, True),
    # unknown dial behaves like the default 'unusual'
    ("weird", "trend", None, None, False),
    ("weird", "transition", "HIGH", None, True),
    # floods: a transition also admits on previous_severity, so the all-clear
    # (severity drops back to NORMAL) still reaches a 'floods' page.
    ("floods", "transition", "NORMAL", "SEVERE HIGH", True),
    ("floods", "transition", "NORMAL", "HIGH", False),
    ("floods", "transition", "HIGH", "SEVERE HIGH", True),
])
def test_alert_allowed_truth_table(sensitivity, alert_type, severity, previous_severity, expected):
    assert alert_allowed(sensitivity, alert_type, severity, previous_severity) is expected
