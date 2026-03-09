"""
River Monitor — Docker entrypoint

Usage: python main.py
"""

import os
import sys
import queue
import signal
import threading
import logging
import logging.handlers

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

LOG_PATH = os.path.join(BASE_DIR, "logs", "river_monitor.log")


def setup_logging():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3
        )
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s"
        ))
        root.addHandler(file_handler)
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(console)


def build_adapters():
    adapters = []
    try:
        from monitor.adapters.telegram import TelegramAdapter
        adapters.append(TelegramAdapter())
    except Exception as e:
        logging.warning("Telegram adapter unavailable: %s", e)
    try:
        from monitor.adapters.sms import SMSAdapter
        adapters.append(SMSAdapter())
    except Exception as e:
        logging.warning("SMS adapter unavailable: %s", e)
    try:
        from monitor.adapters.whatsapp import WhatsAppAdapter
        adapters.append(WhatsAppAdapter())
    except Exception as e:
        logging.warning("WhatsApp adapter unavailable: %s", e)
    try:
        from monitor.adapters.facebook import FacebookAdapter
        adapters.append(FacebookAdapter())
    except Exception as e:
        logging.warning("Facebook adapter unavailable: %s", e)
    return adapters


def main():
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("River Monitor starting")

    from db.models import init_db
    init_db()

    stop_event = threading.Event()

    def handle_signal(signum, frame):
        logger.info("Signal %s received — shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    notif_queue = queue.Queue()

    all_adapters = build_adapters()
    thread_adapters = [a for a in all_adapters if isinstance(a, threading.Thread)]

    from monitor.polling import PollingThread
    from monitor.noaa_polling import NoaaPollingThread
    from monitor.scheduler import SchedulerThread
    from monitor.dispatcher import NotificationDispatcher
    from web.app import create_app

    polling = PollingThread(notif_queue, stop_event=stop_event)
    noaa_polling = NoaaPollingThread(notif_queue, stop_event=stop_event)
    scheduler = SchedulerThread(notif_queue, stop_event=stop_event)
    dispatcher = NotificationDispatcher(
        notif_queue, adapters=all_adapters, stop_event=stop_event
    )

    flask_app = create_app(notification_queue=notif_queue)

    def run_flask():
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
        flask_app.run(host="0.0.0.0", port=5743, use_reloader=False, threaded=True)

    web_thread = threading.Thread(target=run_flask, name="WebThread", daemon=True)

    for t in thread_adapters:
        t.start()
    for t in [polling, noaa_polling, scheduler, dispatcher, web_thread]:
        t.start()

    logger.info("All threads started. Portal at http://localhost:5743")
    stop_event.wait()
    logger.info("Stop event received — shutting down")


if __name__ == "__main__":
    main()
