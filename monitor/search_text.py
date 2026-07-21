"""Shared text helpers for gauge name search — tokenization, full US
state-name expansion, and difflib relevance scoring. Used by both the USGS
(site_search) and NOAA (noaa_search) name searches so ranking is identical."""

import re
from difflib import SequenceMatcher

# Single-word US state names -> the abbreviation used at the end of gauge
# names (e.g. "..., KY"). Multi-word states are intentionally omitted.
_STATE_ABBR = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE",
    "NEVADA": "NV", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WISCONSIN": "WI",
    "WYOMING": "WY",
}


def _tokenize(query):
    """Split user text into uppercase keyword tokens (>= 2 chars)."""
    return [t for t in re.split(r"[^A-Z0-9]+", query.upper()) if len(t) >= 2]


def _score(name, tokens):
    """Relevance score of a station name against the query tokens.

    Each token contributes up to 1.0: 1.0 for an exact substring (or a state
    name whose ", XX" abbreviation is present), else the best difflib
    similarity of the token to any single word in the name.
    """
    up = name.upper()
    words = [w for w in re.split(r"[^A-Z0-9]+", up) if w]
    total = 0.0
    for t in tokens:
        if t in up:
            total += 1.0
            continue
        if t in _STATE_ABBR and f", {_STATE_ABBR[t]}" in up:
            total += 1.0
            continue
        total += max((SequenceMatcher(None, t, w).ratio() for w in words),
                     default=0.0)
    return total
