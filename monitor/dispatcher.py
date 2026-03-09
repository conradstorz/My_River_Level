import threading
import queue
import logging

from db.models import get_db, get_page_subscribers_for_gauge

logger = logging.getLogger(__name__)


def format_transition_message(data):
    return (
        f"⚠️ River Level Change: {data['station_name']} (#{data['site_number']})\n"
        f"Condition changed: {data['previous_severity']} → {data['new_severity']}\n"
        f"Current level: {data['current_value']:.2f} {data['unit']} "
        f"({data['percentile']:.1f}th percentile)"
    )


def format_reminder_message(data):
    return (
        f"🔔 River Level Reminder: {data['station_name']} (#{data['site_number']})\n"
        f"Current condition: {data['severity']}\n"
        f"Level: {data['current_value']:.2f} {data['unit']} "
        f"({data['percentile']:.1f}th percentile)"
    )


def format_noaa_transition_message(data):
    return (
        f"⚠️ River Level Change: {data['station_name']} ({data['lid']})\n"
        f"Condition changed: {data['previous_severity']} → {data['new_severity']}\n"
        f"Current stage: {data['current_stage']:.2f} ft\n"
        f"View: https://water.noaa.gov/gauges/{data['lid'].lower()}"
    )


def get_active_subscribers(db_path=None):
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, channel, channel_id FROM subscribers WHERE active=1"
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def log_notification(subscriber_id, site_id, channel, message, trigger_type, success, error_msg="", db_path=None):
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO notifications
               (subscriber_id, site_id, channel, message_text, trigger_type, success, error_msg)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (subscriber_id, site_id, channel, message, trigger_type, 1 if success else 0, error_msg)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


class NotificationDispatcher(threading.Thread):
    def __init__(self, notification_queue, adapters=None, db_path=None, stop_event=None):
        super().__init__(name="NotificationDispatcher", daemon=True)
        self.queue = notification_queue
        self.adapters = {a.channel: a for a in (adapters or [])}
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()

    def run(self):
        logger.info("NotificationDispatcher started")
        while not self.stop_event.is_set():
            self.run_once()
        logger.info("NotificationDispatcher stopped")

    def run_once(self):
        """Process one item from the queue (blocking with 1s timeout)."""
        try:
            item = self.queue.get(timeout=1)
        except queue.Empty:
            return

        if item is None:
            self.queue.task_done()
            return

        try:
            if item["type"] == "broadcast":
                message = item["data"]["message"]
                allowed_channels = set(item["data"].get("channels", list(self.adapters.keys())))
                trigger_type = "manual"
                site_id = None
                subscribers = [s for s in get_active_subscribers(self.db_path)
                               if s["channel"] in allowed_channels]
                for sub in subscribers:
                    adapter = self.adapters.get(sub["channel"])
                    if adapter is None:
                        continue
                    try:
                        success = adapter.send(sub["channel_id"], message)
                        log_notification(sub["id"], None, sub["channel"], message,
                                        trigger_type, success, db_path=self.db_path)
                    except Exception as e:
                        logger.exception("Failed broadcast to %s/%s", sub["channel"], sub["channel_id"])
                        log_notification(sub["id"], None, sub["channel"], message,
                                        trigger_type, False, str(e), db_path=self.db_path)
                return

            if item["type"] == "transition":
                message = format_transition_message(item["data"])
                trigger_type = "transition"
                site_id = item["data"]["site_id"]
            elif item["type"] == "reminder":
                message = format_reminder_message(item["data"])
                trigger_type = "reminder"
                site_id = item["data"]["site_id"]
            elif item["type"] == "noaa_transition":
                message = format_noaa_transition_message(item["data"])
                trigger_type = "noaa_transition"
                gauge_id = item["data"]["gauge_id"]
                subscribers = get_page_subscribers_for_gauge(gauge_id, self.db_path)
                for sub in subscribers:
                    adapter = self.adapters.get(sub["channel"])
                    if adapter is None:
                        continue
                    try:
                        success = adapter.send(sub["channel_id"], message)
                        log_notification(None, None, sub["channel"],
                                         message, trigger_type, success, db_path=self.db_path)
                    except Exception as e:
                        logger.exception("Failed noaa notify to %s/%s", sub["channel"], sub["channel_id"])
                        log_notification(None, None, sub["channel"],
                                         message, trigger_type, False, str(e), db_path=self.db_path)
                return
            else:
                logger.warning("Unknown notification type: %s", item.get("type"))
                return

            subscribers = get_active_subscribers(self.db_path)
            for sub in subscribers:
                adapter = self.adapters.get(sub["channel"])
                if adapter is None:
                    continue
                try:
                    success = adapter.send(sub["channel_id"], message)
                    log_notification(sub["id"], site_id, sub["channel"],
                                     message, trigger_type, success, db_path=self.db_path)
                except Exception as e:
                    logger.exception("Failed to send to %s/%s", sub["channel"], sub["channel_id"])
                    log_notification(sub["id"], site_id, sub["channel"],
                                     message, trigger_type, False, str(e), db_path=self.db_path)
        except Exception:
            logger.exception("Error processing notification item")
        finally:
            self.queue.task_done()
