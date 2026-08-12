"""Tests for the process supervisor in main.py.

The supervisor is what turns a half-dead container into a restarted one. Before
it existed, main() blocked on stop_event.wait() forever, so if the polling
thread died the container still reported "Up" and Docker never restarted it —
monitoring was silently dead until someone noticed no alerts had arrived.
"""

import logging
import threading

import main


logger = logging.getLogger(__name__)


class _FakeThread:
    """Stands in for a worker thread with a controllable liveness."""

    def __init__(self, alive):
        self._alive = alive

    def is_alive(self):
        return self._alive


def test_supervise_returns_false_when_a_critical_thread_dies(monkeypatch):
    monkeypatch.setattr(main, "SUPERVISOR_INTERVAL_SECONDS", 0.01)
    registry = {
        "PollingThread": _FakeThread(False),
        "NotificationDispatcher": _FakeThread(True),
    }
    assert main.supervise(threading.Event(), registry, logger) is False


def test_supervise_keeps_running_when_only_a_non_critical_thread_dies(monkeypatch):
    """An unconfigured channel adapter exiting is normal, not a reason to restart."""
    monkeypatch.setattr(main, "SUPERVISOR_INTERVAL_SECONDS", 0.01)
    stop_event = threading.Event()
    registry = {
        "PollingThread": _FakeThread(True),
        "TelegramAdapter": _FakeThread(False),
    }

    checks = []
    real_wait = stop_event.wait

    def counting_wait(timeout=None):
        checks.append(1)
        if len(checks) >= 3:
            stop_event.set()
        return real_wait(0)

    stop_event.wait = counting_wait
    assert main.supervise(stop_event, registry, logger) is True
    assert len(checks) >= 3


def test_supervise_returns_true_on_clean_shutdown(monkeypatch):
    monkeypatch.setattr(main, "SUPERVISOR_INTERVAL_SECONDS", 0.01)
    stop_event = threading.Event()
    stop_event.set()
    registry = {"PollingThread": _FakeThread(True)}
    assert main.supervise(stop_event, registry, logger) is True


def test_every_worker_thread_name_is_covered_by_critical_threads():
    """A worker whose name drifts out of CRITICAL_THREADS would die unnoticed."""
    from monitor.polling import PollingThread
    from monitor.noaa_polling import NoaaPollingThread
    from monitor.forecast_polling import ForecastPollingThread
    from monitor.scheduler import SchedulerThread
    from monitor.dispatcher import NotificationDispatcher

    import queue
    q = queue.Queue()
    names = {
        PollingThread(q).name,
        NoaaPollingThread(q).name,
        ForecastPollingThread().name,
        SchedulerThread(q).name,
        NotificationDispatcher(q).name,
    }
    assert names <= set(main.CRITICAL_THREADS)
