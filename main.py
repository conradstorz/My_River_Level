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
    """Configure root logging with a rotating file handler and console output."""
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
    """Instantiate every notification adapter, skipping any that fail to load."""
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


#: Threads whose death means the service is no longer doing its job. If one of
#: these dies we exit the process so Docker's restart policy brings us back —
#: previously main() blocked forever and the container stayed "Up" while
#: monitoring was silently dead.
CRITICAL_THREADS = (
    "PollingThread",
    "NoaaPollingThread",
    "ForecastPollingThread",
    "SchedulerThread",
    "NotificationDispatcher",
    "WebThread",
)

SUPERVISOR_INTERVAL_SECONDS = 60


def supervise(stop_event, thread_registry, logger):
    """Watch the worker threads until shutdown, or until a critical one dies.

    Returns True for a clean shutdown and False when a critical thread died
    and the process should exit non-zero so Docker restarts it.
    """
    while not stop_event.wait(timeout=SUPERVISOR_INTERVAL_SECONDS):
        dead = [name for name, t in thread_registry.items() if not t.is_alive()]
        critical = [name for name in dead if name in CRITICAL_THREADS]
        if critical:
            logger.critical(
                "Worker thread(s) died: %s — exiting so Docker restarts us", critical
            )
            return False
        if dead:
            logger.warning("Non-critical thread(s) not running: %s", dead)
    return True


def main():
    """Initialize the DB, start all worker threads and the web server, then supervise."""
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("River Monitor starting")

    from db.models import init_db
    init_db()

    stop_event = threading.Event()

    def handle_signal(signum, frame):
        """Set the stop event on SIGTERM/SIGINT to trigger a graceful shutdown."""
        logger.info("Signal %s received — shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    notif_queue = queue.Queue()

    all_adapters = build_adapters()
    thread_adapters = [a for a in all_adapters if isinstance(a, threading.Thread)]

    from monitor.polling import PollingThread
    from monitor.noaa_polling import NoaaPollingThread
    from monitor.forecast_polling import ForecastPollingThread
    from monitor.scheduler import SchedulerThread
    from monitor.dispatcher import NotificationDispatcher
    from web.app import create_app

    polling = PollingThread(notif_queue, stop_event=stop_event)
    noaa_polling = NoaaPollingThread(notif_queue, stop_event=stop_event)
    forecast_polling = ForecastPollingThread(stop_event=stop_event)
    scheduler = SchedulerThread(notif_queue, stop_event=stop_event)
    dispatcher = NotificationDispatcher(
        notif_queue, adapters=all_adapters, stop_event=stop_event
    )

    workers = [polling, noaa_polling, forecast_polling, scheduler, dispatcher]
    thread_registry = {t.name: t for t in workers + thread_adapters}

    flask_app = create_app(
        notification_queue=notif_queue, thread_registry=thread_registry
    )

    def run_flask():
        """Serve the portal on 0.0.0.0:5743 with waitress.

        Not Flask's built-in server: that one is a development tool and is not
        built to stay up under continuous production use.
        """
        from waitress import serve
        serve(flask_app, host="0.0.0.0", port=5743, threads=8)

    web_thread = threading.Thread(target=run_flask, name="WebThread", daemon=True)
    thread_registry[web_thread.name] = web_thread

    for t in thread_adapters:
        t.start()
    for t in workers + [web_thread]:
        t.start()

    logger.info("All threads started. Portal at http://localhost:5743")
    clean = supervise(stop_event, thread_registry, logger)
    if not clean:
        sys.exit(1)
    logger.info("Stop event received — shutting down")


if __name__ == "__main__":
    main()
