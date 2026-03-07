"""
Phone number normalization utilities.

Ensures phone numbers stored in the database and sent to Twilio are in
E.164 format (+[country code][number]), which is what Twilio requires.
"""

import re


def normalize_e164(number, default_country_code="1"):
    """
    Normalize a phone number to E.164 format.

    Handles:
        '8125577095'      → '+18125577095'  (bare 10-digit US)
        '18125577095'     → '+18125577095'  (11-digit with leading 1)
        '+18125577095'    → '+18125577095'  (already E.164)
        '(812) 557-7095'  → '+18125577095'  (formatted US)
        '+447911123456'   → '+447911123456' (international, preserved)
        ''                → ''             (empty, returned as-is)

    For numbers that cannot be matched to a known pattern, returns the
    input unchanged rather than silently corrupting it.
    """
    if not number:
        return number

    # If it already starts with +, strip formatting and return
    if number.startswith("+"):
        digits = re.sub(r"\D", "", number)
        return f"+{digits}"

    digits = re.sub(r"\D", "", number)

    if len(digits) == 10:
        return f"+{default_country_code}{digits}"

    if len(digits) == 11 and digits[0] == default_country_code:
        return f"+{digits}"

    # Unknown format — return original so we don't silently corrupt it
    return number
