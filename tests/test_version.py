import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import version


def test_version_string():
    assert version.VERSION == "1.0.0"


def test_release_date_string():
    assert version.RELEASE_DATE == "2026-03-08"
