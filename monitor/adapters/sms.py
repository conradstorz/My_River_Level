"""Notification adapter for SMS via Twilio."""

import logging
from db.models import get_setting
from monitor.phone_utils import normalize_e164

logger = logging.getLogger(__name__)

try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False


class SMSAdapter:
    """Sends alerts as SMS text messages through the Twilio REST API."""

    channel = "sms"

    def __init__(self, db_path=None):
        """Initialize the adapter with an optional DB path for reading credentials."""
        self.db_path = db_path

    def _get_client(self):
        """Build a Twilio client and SMS sender number from settings, or (None, None) if unconfigured."""
        sid = get_setting("twilio_account_sid", self.db_path)
        token = get_setting("twilio_auth_token", self.db_path)
        if not sid or not token:
            return None, None
        return Client(sid, token), get_setting("twilio_sms_number", self.db_path)

    def send(self, to_number, message):
        """Send an SMS to the given number; return True on success, False otherwise."""
        if not TWILIO_AVAILABLE:
            logger.error("twilio not installed")
            return False
        client, from_number = self._get_client()
        if client is None:
            logger.warning("Twilio credentials not configured")
            return False
        try:
            client.messages.create(
                body=message,
                from_=from_number,
                to=normalize_e164(to_number),
            )
            return True
        except Exception as e:
            logger.error("SMS send failed to %s: %s", to_number, e)
            return False
