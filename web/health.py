"""Unauthenticated liveness endpoint for the River Monitor container.

``main.py`` hands the app a ``thread_registry`` — a ``dict[str,
threading.Thread]`` covering the polling, scheduler, and dispatcher workers —
and ``GET /healthz`` reports whether each is still alive. A dead worker makes
the endpoint return 503 so Docker's healthcheck can restart a container that
is still serving pages but has stopped doing any monitoring.

When no registry is configured (tests, or a portal-only process) the endpoint
reports ``{"status": "ok", "threads": {}}``: nothing was claimed, so nothing
is missing.
"""

from flask import jsonify


def register_health(app):
    """Attach ``GET /healthz`` (endpoint name ``healthz``) to the app."""

    @app.route("/healthz")
    def healthz():
        """GET /healthz — 200 when every registered thread is alive, else 503."""
        registry = app.config.get("THREAD_REGISTRY") or {}
        threads = {name: bool(thread.is_alive()) for name, thread in registry.items()}
        healthy = all(threads.values())
        payload = {"status": "ok" if healthy else "degraded", "threads": threads}
        return jsonify(payload), (200 if healthy else 503)
