"""
Tests for phone number E.164 normalization.

Root cause captured: subscriber phone number '8125577095' was stored without
a country code prefix. Twilio silently normalized it to +18125577095, but
relying on this is fragile. Normalization must happen at registration time.

Additionally, even with a correct number, messages were undelivered due to
Twilio error 30034 (A2P 10DLC campaign not registered). The status callback
webhook is the mechanism that surfaces these async failures.
"""

import pytest
from monitor.phone_utils import normalize_e164


# ── normalize_e164 ────────────────────────────────────────────────────────────

def test_10_digit_us_number_gets_plus_one_prefix():
    """Bare 10-digit US numbers must become +1XXXXXXXXXX."""
    assert normalize_e164("8125577095") == "+18125577095"


def test_already_e164_is_unchanged():
    assert normalize_e164("+18125577095") == "+18125577095"


def test_11_digit_starting_with_1_gets_plus():
    assert normalize_e164("18125577095") == "+18125577095"


def test_number_with_dashes_normalized():
    assert normalize_e164("812-557-7095") == "+18125577095"


def test_number_with_parens_and_spaces_normalized():
    assert normalize_e164("(812) 557-7095") == "+18125577095"


def test_international_number_with_plus_preserved():
    """Non-US numbers that already have + prefix should be preserved."""
    result = normalize_e164("+447911123456")
    assert result == "+447911123456"


def test_empty_string_returned_as_is():
    """An empty string should come back unchanged rather than raise."""
    assert normalize_e164("") == ""
