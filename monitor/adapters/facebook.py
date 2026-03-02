import logging
import requests as req
from db.models import get_setting

logger = logging.getLogger(__name__)

SEND_API_URL = "https://graph.facebook.com/v19.0/me/messages"


class FacebookAdapter:
    channel = "facebook"

    def __init__(self, db_path=None):
        self.db_path = db_path

    def send(self, psid, message):
        """
        psid: Page-Scoped User ID (the subscriber's Facebook ID)
        """
        token = get_setting("facebook_page_token", self.db_path)
        if not token:
            logger.warning("Facebook page token not configured")
            return False
        try:
            response = req.post(
                SEND_API_URL,
                params={"access_token": token},
                json={
                    "recipient": {"id": psid},
                    "message": {"text": message}
                },
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error("Facebook send failed to psid %s: %s", psid, e)
            return False
