import logging
from db.models import get_setting

logger = logging.getLogger(__name__)

try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False


class WhatsAppAdapter:
    channel = "whatsapp"

    def __init__(self, db_path=None):
        self.db_path = db_path

    def _get_client(self):
        sid = get_setting("twilio_account_sid", self.db_path)
        token = get_setting("twilio_auth_token", self.db_path)
        if not sid or not token:
            return None, None
        number = get_setting("twilio_whatsapp_number", self.db_path)
        return Client(sid, token), f"whatsapp:{number}"

    def send(self, to_number, message):
        """to_number should be in format '+15551234567' (no whatsapp: prefix)."""
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
                to=f"whatsapp:{to_number}"
            )
            return True
        except Exception as e:
            logger.error("WhatsApp send failed to %s: %s", to_number, e)
            return False
