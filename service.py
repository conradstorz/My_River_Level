"""
River Monitor Windows Service

Install:   python service.py install
Start:     python service.py start
Stop:      python service.py stop
Remove:    python service.py remove
Debug run: python service.py debug
"""

import os
import sys
import queue
import threading
import logging
import logging.handlers
import importlib.util

# Ensure project root is on path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DB_PATH = os.path.join(BASE_DIR, "db", "river_monitor.db")
LOG_PATH = os.path.join(BASE_DIR, "logs", "river_monitor.log")

try:
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False


def setup_logging():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Avoid adding duplicate handlers if called multiple times
    if not root.handlers:
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s"
        ))
        root.addHandler(handler)
        # Also log to console in debug mode
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(console)


def migrate_legacy_config(db_path):
    """Import config.py and seed the database if it exists."""
    config_path = os.path.join(BASE_DIR, "config.py")
    if not os.path.exists(config_path):
        return
    try:
        spec = importlib.util.spec_from_file_location("config", config_path)
        config_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config_module)
        from db.migration import migrate_from_config
        migrate_from_config(config_module, db_path)
        logging.getLogger(__name__).info("Migrated config.py to database")
    except Exception:
        logging.getLogger(__name__).exception("Error migrating config.py")


def build_adapters(db_path):
    """Instantiate all channel adapters that are available."""
    adapters = []
    try:
        from monitor.adapters.telegram import TelegramAdapter
        adapters.append(TelegramAdapter(db_path=db_path))
    except Exception as e:
        logging.warning("Telegram adapter unavailable: %s", e)
    try:
        from monitor.adapters.sms import SMSAdapter
        adapters.append(SMSAdapter(db_path=db_path))
    except Exception as e:
        logging.warning("SMS adapter unavailable: %s", e)
    try:
        from monitor.adapters.whatsapp import WhatsAppAdapter
        adapters.append(WhatsAppAdapter(db_path=db_path))
    except Exception as e:
        logging.warning("WhatsApp adapter unavailable: %s", e)
    try:
        from monitor.adapters.facebook import FacebookAdapter
        adapters.append(FacebookAdapter(db_path=db_path))
    except Exception as e:
        logging.warning("Facebook adapter unavailable: %s", e)
    return adapters


def run_service(db_path=None, stop_event=None):
    """Start all threads. Blocks until stop_event is set."""
    db_path = db_path or DB_PATH
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("River Monitor starting (db: %s)", db_path)

    from db.models import init_db
    init_db(db_path)
    migrate_legacy_config(db_path)

    stop_event = stop_event or threading.Event()
    notif_queue = queue.Queue()

    all_adapters = build_adapters(db_path)

    # Separate thread-based adapters (Telegram) from plain adapters
    thread_adapters = [a for a in all_adapters if isinstance(a, threading.Thread)]
    # All adapters go to dispatcher (including Telegram which is also a Thread)
    plain_adapters = all_adapters

    from monitor.polling import PollingThread
    from monitor.noaa_polling import NoaaPollingThread
    from monitor.scheduler import SchedulerThread
    from monitor.dispatcher import NotificationDispatcher
    from web.app import create_app

    polling = PollingThread(notif_queue, db_path=db_path, stop_event=stop_event)
    noaa_polling = NoaaPollingThread(notif_queue, db_path=db_path, stop_event=stop_event)
    scheduler = SchedulerThread(notif_queue, db_path=db_path, stop_event=stop_event)
    dispatcher = NotificationDispatcher(
        notif_queue, adapters=plain_adapters, db_path=db_path, stop_event=stop_event
    )

    # Flask web thread
    flask_app = create_app(db_path=db_path, notification_queue=notif_queue)

    def run_flask():
        import logging as _log
        _log.getLogger("werkzeug").setLevel(_log.WARNING)
        flask_app.run(host="127.0.0.1", port=5743, use_reloader=False, threaded=True)

    web_thread = threading.Thread(target=run_flask, name="WebThread", daemon=True)

    # Start all threads
    for t in thread_adapters:
        t.start()
    for t in [polling, noaa_polling, scheduler, dispatcher, web_thread]:
        t.start()

    logger.info("All threads started. Portal at http://localhost:5743")
    stop_event.wait()
    logger.info("Stop event received — shutting down")


if WIN32_AVAILABLE:
    class RiverMonitorService(win32serviceutil.ServiceFramework):
        _svc_name_ = "RiverMonitor"
        _svc_display_name_ = "River Level Monitor Service"
        _svc_description_ = "Monitors USGS stream gauges and sends condition alerts."

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.stop_event = threading.Event()
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.stop_event.set()
            win32event.SetEvent(self.hWaitStop)

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, "")
            )
            run_service(stop_event=self.stop_event)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "debug":
        print("Running in debug mode — press Ctrl+C to stop")
        print("Portal: http://localhost:5743")
        try:
            run_service()
        except KeyboardInterrupt:
            print("\nShutting down...")
    elif WIN32_AVAILABLE:
        win32serviceutil.HandleCommandLine(RiverMonitorService)
    else:
        print("pywin32 not available. Run: pip install pywin32")
        print("Or use: python service.py debug")
